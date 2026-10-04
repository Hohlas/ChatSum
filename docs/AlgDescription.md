# ChatSum — Algorithm Description (agent onboarding)

Telegram summarizer bot, one codebase, **two runtime modes — use exactly one
at a time** (two live processes on one Telegram session desync updates and can
kill the authorization; README §"Выбор площадки"). Repo: `Hohlas/ChatSum`. Read this file
first; it replaces reading the whole code. Numbers below are load-bearing —
do not "tune" them without the owner.

## Components

- `main.py` — core: Telegram client, `scheduled_analysis_job` (scheduled slot),
  `run_analysis` (one inbox command; returns True only if ALL `send_*` passed),
  Gemini key rotation (`select_google_api_key_for_new_analysis`, round-robin;
  `rotate_*` on 503/quota).
- `run_once.py` — scheduler + duty wrapper. Imports `main.py`. Entry points:
  `--due` (manual drain), `--watch` (duty loop). All times in slots are
  **MSK = UTC+3** (`MSK`, `run_once.py:39`).
- `SCHEDULE.txt` (on `main`) — slots `chat_id|HH:MM|limit`, one per line.
  Leading `+` is stripped. Limit `10` = cheap probe, `1d` = full summary.
- `.github/workflows/summarize.yml` — cron `*/11 * * * *` UTC (11 is coprime
  with 60, ticks drift away from the :00 load peak), `timeout-minutes: 330`,
  **no `concurrency`** (overlap is the design, not an accident).
  Inputs `watch_seconds` (default 18000 = 5h), `poll_interval` (default 30).
- `state` branch (GitHub Contents API, `STATE_DEFAULT`, `run_once.py:40`):
  `state.json` {`completed`, `last_run_utc`, `google_key_cursor`,
  `inbox_attempts`, `due_fails`}, `leader.json`, `standby.json`.
- Run scripts (VPS only, in `scripts/`): `start_bot.sh` (foreground),
  `start_bot_background.sh` (`screen`), `stop_bot.sh`, `setup.sh` (venv + deps
  + `private.txt` scaffold), `telegram-bot.service.example` (systemd unit).
  Secrets for Actions are pushed by `scripts/push_github_secrets.sh` (reads
  local `private.txt` + session, uploads to repo Secrets/Variables, never
  prints values). One-off helpers live in `tools/` (`gen_session.py`,
  `get_channel_id.py`, legacy `test_bot.py`); the suite is `tests/test_run_once.py`.

## VPS mode (long-lived userbot, the original mode)

Entry: `python3 main.py` → `main()` (`main.py:4555`) → Telethon client
`run_until_disconnected()`. One process holds one Telegram session
(`session_name.session` file; `telegram_session.txt` holds the StringSession
copy). Config is local `private.txt` (+ `PROMPT.txt`, `EXCLUDED_USERS.txt`,
`PRIORITY_USERS.txt`, `SCHEDULE.txt` from the working tree).

- Commands are **outgoing slash-commands from the owner's own account**
  (`@telegram_client.on(events.NewMessage(outgoing=True, ...))`,
  `main.py:3904+`): `/sum` (`3h` / `45` / `600-800` / `2d-3d` / `1d+` — the
  `+` also posts the result back into the source chat), `/copy` (same ranges,
  no AI = no API cost), `/config` family (`/show_model`, `/set_model`,
  `/add_excluded`, `/reload_config`, …), `/sch HH:MM period` + `/sch_list` +
  `/unsch`, `/help`. Send `/sum …` in any chat, the userbot answers there.
- Schedule: APScheduler `AsyncIOScheduler` (`main.py:3851`); `reload_schedule()`
  (`main.py:3889`) registers one `CronTrigger` per slot in MSK
  (`scheduled_analysis_job`, `main.py:3854` → `run_analysis(…, scheduled=True)`).
- No dedup layer: a trigger fires once by the clock. If the process is down
  at fire time, **that slot is silently missed** — no catch-up (this exact
  weakness is why the Actions mode uses per-key `completed` marks instead).
- Live management (`/set_model`, `/reload_config`, `/sch` edits) exists ONLY
  here. In Actions mode these commands do not exist; model comes from
  `GEMINI_*` repo Variables, schedule only via git commits to `SCHEDULE.txt`.

## GitHub Actions mode (duty on cron, current production mode)

No long-lived process: short-lived workflow runs form a relay — cron tick
`*/11` UTC starts a run, the run works as leader or standby for up to 5h,
then the next tick takes over.

## Leader iteration (the hot loop, `_run_leader_loop`, `run_once.py:1633`)

Each iteration, strictly in this order:

1. **Inbox first** — `poll_inbox_first` (`run_once.py:1494`): deep first pass
   with `offset_id` pagination (10×100) so commands buried under summaries
   during a gap are not skipped. Processing via `_process_batch` /
   `process_inbox_message` (`run_once.py:1467`/`1275`). Before starting an
   analysis the message is re-read (claim-check vs double-take by a rival).
   Failure does NOT delete the command: `inbox_attempts` counter up to
   `INBOX_MAX_ATTEMPTS=5` with "🔁 Попытка N/5" progress; `last_seen` rolls
   back to (oldest retry − 1) so retry comes in ~30s, not after restart.
   Only success or exhaustion deletes the command.
2. **At most one due task** — `run_due_once(..., max_tasks=1)`
   (`MAX_DUE_PER_ITERATION`, `run_once.py:186`; manual `--due` passes
   None = drain all). Per-key dedup: `compute_due(entries, now, completed)`
   (`run_once.py:591`) — a slot fires once per key `chat|HH:MM|limit` per
   occurrence date. After 3 consecutive failures a slot is skipped for 3h
   (`due_skip_info`, `run_once.py:688`; quota protection). Success clears.
   Worst inbox delay ≈ one summary duration.
3. Background: `_heartbeat_loop` (`run_once.py:1614`, own asyncio task)
   re-claims + pushes every `LEADER_HEARTBEAT_SEC` (interleaves on Gemini/
   Telegram awaits — a long summary must NOT look like a dead leader).
   State push every 300s, `SCHEDULE.txt` refresh from `origin/main` every
   `SCHEDULE_REFRESH_SEC` (a 5h run would otherwise go stale).

Cleanup (`_watch_cleanup`, `run_once.py:1547`) ALWAYS pull-merges first,
then saves/pushes. The workflow's trailing `Persist state` step does the
same. Rationale: a silent standby or a yielding leader must only ever write
a **superset** — never wipe чужой прогресс.

## Duty protocol (leader ↔ standby, all via Contents API files)

- Claim: `leader_claim` (`run_once.py:369`) — check-then-PUT, last-writer-wins
  + one retry on sha race. New run claims `ready:false`, then `ready:true`
  only after Telegram connect succeeds (a run that can't reach Telegram
  must never look like a leader).
- Newcomer vs live leader → `_run_standby_loop` (`run_once.py:1690`): no
  Telegram, only GETs the flag every 30s; single shared `standby.json`.
- `leader_should_yield` (`run_once.py:239`): yield only to a live + ready +
  strictly newer leader; equal start time → bigger `run_id` wins.
- `standby_should_promote` (`run_once.py:263`): missing flag, stale heartbeat,
  or `retiring` leader — confirmed over `PROMOTE_CONFIRM_READS` consecutive
  reads (no flapping on one slow GET). A leader near its deadline writes
  `retiring` + pushes → handoff is instant.
- Split-brain resolution is always "newer wins"; duplicates are absorbed by
  the inbox claim-check + per-key due dedup.

## State merge — the one invariant that matters

`merge_states(local, remote)` (`run_once.py:419`) is a **union**, never an
intersection: `completed` keeps the fresher date per key, counters take
`max()` (incl. `google_key_cursor` — a local value must never roll the
rotation back), `last_run_utc` takes max. `prune_state` (`run_once.py:93`)
drops counters older than 3 days. Consequence: under a single writer,
pushed content is monotonically non-decreasing in keys.

`push_state_best_effort` (`run_once.py:467`): GET → merge → PUT → on sha
conflict one re-GET/merge/PUT. Then `_verify_push` (`run_once.py:492`):
re-read the branch, confirm all local `completed` keys are present;
on mismatch one more PUT + loud `🚨` log (tripwire, see Incidents §4).
A run never exits nonzero over state trouble — worst case the trailing
`Persist state` workflow step carries the local file.

## Load-bearing numbers (Actions mode only — VPS timing is just the APScheduler clock)

| Symbol | Value | Meaning |
|---|---|---|
| cron / timeout | `*/11`, 330 min | schedule drift; 5h watch + setup + tail analysis |
| watch / poll defaults | 18000s / 30s | duty length / inbox latency while leader lives |
| `LEADER_HEARTBEAT_SEC` / `LEADER_STALE_SEC` | 120 / 300 | liveness bound ≈ 2 missed beats |
| `SCHEDULE_REFRESH_SEC` | 600 | schedule staleness inside a 5h run |
| `PROMOTE_CONFIRM_READS` | 2 | anti-flap on promotion |
| `INBOX_MAX_ATTEMPTS` | 5 | command retries before deletion |
| `DUE_FAIL_THRESHOLD` / `DUE_FAIL_SKIP_SEC` | 3 / 10800 | skip poisoned slot 3h |
| `MAX_DUE_PER_ITERATION` | 1 | inbox-first latency bound |

## Incidents that shaped the design (don't re-learn)

1. LAG-window catch-up dropped slots when cron gaps exceeded the window →
   per-key `completed` dedup replaced windows entirely (`LAG_MAX`,
   `_last_run_msk_naive` deleted).
2. `timeout-minutes: 12` killed a run mid-analysis; command survived,
   un-deleted → looked like a "repeat". Timeout is 330 now; undone work
   retries via `inbox_attempts`, not via restarts.
3. Heartbeat sent once per N iterations froze during long summaries →
   leader looked dead mid-task. Heartbeat is a separate asyncio task now.
4. Due-drain storm (all unmarked slots in a row, tens of minutes) starved a
   valid inbox command → inbox-first + `MAX_DUE_PER_ITERATION=1`.
5. **OPEN (2026-10-04):** E2E run logged 5× `✅ выполнено, записано в state`
   (save runs strictly before that print) yet branch pushes + final Persist
   contained only 1 mark; no errors, single writer, all write paths
   union-safe. Static analysis found no loss path; live re-test with current
   code persisted 6/6 marks. Residual hypothesis: transient Contents-API
   inconsistency on rapid successive PUTs. Tripwire: `_verify_push` + `🚨`.
   Repro harness (ephemeral, NOT in repo): `/tmp/opencode/repro/replay.py`.

## Do-not-touch / discipline

- Secrets: `private.txt`, `telegram_session.txt`, `*.session` — gitignored,
  live in GitHub Secrets + locally only. Never commit, never print.
- `docs/CONTEXT_HANDOFF.md` = the *now* (live run ids, today's observations);
  stable knowledge lives HERE, not there. One source of truth each.
- Tests: `./venv/bin/python tests/test_run_once.py` must stay green (156 PASS as of
  2026-10-04; incl. `test_duty`, `test_heartbeat_loop`, `test_due_cap`,
  `test_inbox_retry_flow`, verify tests). YAML validity is asserted too.
