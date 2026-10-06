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
   `rotate_*` on 503/quota — rotate also advances the persistent counter, so the
   next analysis starts past dead keys instead of re-walking them).
   Flat quota rule (2026-10-06): 429-quota → instant rotate, ONE hit per key,
   no same-key retries/sleeps (a dry key won't get wet from retries; 9 keys ×
   30s of sleeps was ~5 min of dead waiting per circle). Pause
   (`FULL_CIRCLE_FAIL_PAUSE_SEC=60`) happens ONLY after a fully lost circle
   (all keys refused), never per rotation. 503/UNAVAILABLE: hold the SAME key
   with growing pauses (`SERVER_RETRY_PAUSES_SEC=60/180/540`), NO key walk.
   Why: (1) the storm is backend-wide — the next key hits the same wall;
   (2) a 503 is charged against the daily limit — чужой замер через дашборд
   AI Studio: 31 из 50 засчитанных запросов были 503 «The model is overloaded»
   («Every time you send a request and receive a 503 response, Google records
   it as successful and counts your quota», dev forum Nov 2025; no official
   line in the docs — secondary data, but two independent observations plus
   the «503 lands AFTER key authentication» mechanics agree — do NOT redesign
   back into key-walking); (3) official guidance for 503 is «backoff and retry
   with a long maximum delay» (Gemini team reply, dev forum Feb 2026) —
   waiting is literally what is asked. After 3 tries the chunk fails outward.
   Sustained 503 also stops remaining chunks (`stop_due_to_overload`, mirror
   of the quota stop), so a multi-chunk summary doesn't sleep 13 min per
   chunk. 500/502/504 and timeouts deliberately stay on the old short-retry
   path (second-long blips; timeouts are ambiguous client/network-side).
   TG 429 line carries quotaId + retry (`format_quota_diagnostic`; retry also
   parsed from message text 'Please retry in 17h1m40s').
- `run_once.py` — scheduler + duty wrapper. Imports `main.py`. Entry points:
  `--due` (manual drain), `--watch` (duty loop). All times in slots are
  **MSK = UTC+3** (`MSK`, `run_once.py:39`).
- `SCHEDULE.txt` (on `main`) — slots `chat_id|HH:MM|limit`, one per line.
  Leading `+` is stripped. Limit `10` = cheap probe, `1d` = full summary.
- `.github/workflows/summarize.yml` — cron `*/31 * * * *` UTC (31 is coprime
  with 60, ticks drift away from the :00 load peak; sparser than the old */11 —
  GitHub silently thins out frequent crons), `timeout-minutes: 355`
  (platform max 360; the kill is identical to GitHub's — ours only lands
  earlier and predictably), **no `concurrency`** (overlap is the design,
  not an accident). Budget: watch 330 + tail 15 + setup 1 = 346 ≤ 355.
  Inputs `watch_seconds` (default 19800 = 5.5h), `poll_interval` (default 30).
- `state` branch (GitHub Contents API, `STATE_DEFAULT`, `run_once.py:40`):
  `state.json` {`completed`, `last_run_utc`, `google_key_cursor`,
  `inbox_attempts`, `due_fails`}, `leader.json`, `standby.json`,
  `work.json` {`run_id`, `last_work_utc`} (progress marker, §Duty protocol).
- Run scripts (VPS only, in `scripts/`): `start_bot.sh` (foreground),
  `start_bot_background.sh` (`screen`), `stop_bot.sh`, `setup.sh` (venv + deps
  + `private.txt` scaffold), `telegram-bot.service.example` (systemd unit).
  Secrets for Actions are pushed by `scripts/push_github_secrets.sh` (reads
  local `private.txt` + session, uploads to repo Secrets/Variables, never
  prints values). One-off helpers live in `tools/` (`gen_session.py`,
  `get_channel_id.py`, legacy `test_bot.py`); the suite is `tests/test_run_once.py`.

## VPS mode (long-lived userbot, the original mode)

Entry: `python3 main.py` → `main()` (`main.py:4771`) → Telethon client
`run_until_disconnected()`. One process holds one Telegram session
(`session_name.session` file; `telegram_session.txt` holds the StringSession
copy). Config is local `private.txt` (+ `PROMPT.txt`, `EXCLUDED_USERS.txt`,
`PRIORITY_USERS.txt`, `SCHEDULE.txt` from the working tree).

- Commands are **outgoing slash-commands from the owner's own account**
  (`@telegram_client.on(events.NewMessage(outgoing=True, ...))`,
  `main.py:4120+`): `/sum` (`3h` / `45` / `600-800` / `2d-3d` / `1d+` — the
  `+` also posts the result back into the source chat), `/copy` (same ranges,
  no AI = no API cost), `/config` family (`/show_model`, `/set_model`,
  `/add_excluded`, `/reload_config`, …), `/sch HH:MM period` + `/sch_list` +
  `/unsch`, `/help`. Send `/sum …` in any chat, the userbot answers there.
- Schedule: APScheduler `AsyncIOScheduler` (`main.py:4067`); `reload_schedule()`
  (`main.py:4105`) registers one `CronTrigger` per slot in MSK
  (`scheduled_analysis_job`, `main.py:4070` → `run_analysis(…, scheduled=True)`).
- No dedup layer: a trigger fires once by the clock. If the process is down
  at fire time, **that slot is silently missed** — no catch-up (this exact
  weakness is why the Actions mode uses per-key `completed` marks instead).
- Live management (`/set_model`, `/reload_config`, `/sch` edits) exists ONLY
  here. In Actions mode these commands do not exist; model comes from
  `GEMINI_*` repo Variables, schedule only via git commits to `SCHEDULE.txt`.

## GitHub Actions mode (duty on cron, current production mode)

No long-lived process: short-lived workflow runs form a relay — cron tick
`*/31` UTC starts a run, the run works as leader or standby for up to 5.5h,
then the next tick takes over.

## Leader iteration (the hot loop, `_run_leader_loop`, `run_once.py:1940`)

Each iteration, strictly in this order:

1. **Inbox first** — `poll_inbox_first` (`run_once.py:1771`): deep first pass
with `offset_id` pagination (10×100) so commands buried under summaries
during a gap are not skipped. Processing via `_process_batch` /
`process_inbox_message` (`run_once.py:1744`/`1552`). Before starting an
   analysis the message is re-read (claim-check vs double-take by a rival).
   Failure does NOT delete the command: `inbox_attempts` counter up to
   `INBOX_MAX_ATTEMPTS=5` with "🔁 Попытка N/5" progress; `last_seen` rolls
   back to (oldest retry − 1) so retry comes in ~30s, not after restart.
   Only success or exhaustion deletes the command.
2. **At most one due task** — `run_due_once(..., max_tasks=1)`
    (`MAX_DUE_PER_ITERATION`, `run_once.py:186`; manual `--due` passes
    None = drain all). Per-key dedup: `compute_due(entries, now, completed)`
    (`run_once.py:847`) — a slot fires once per key `chat|HH:MM|limit` per
    occurrence date. After 3 consecutive failures a slot is skipped for 1h
    (`due_skip_info`, `run_once.py:968`; quota protection). Success clears.
    Worst inbox delay ≈ one summary duration.
3. Background: `_heartbeat_loop` (`run_once.py:1913`, own asyncio task)
    re-claims + pushes every `LEADER_HEARTBEAT_SEC` (interleaves on Gemini/
    Telegram awaits — a long summary must NOT look like a dead leader).
    State + work-marker push ride the same 120s rhythm,
    `SCHEDULE.txt` refresh from `origin/main` every
    `SCHEDULE_REFRESH_SEC` (a 5.5h run would otherwise go stale).

Cleanup (`_watch_cleanup`, `run_once.py:1824`) ALWAYS pull-merges first,
then saves/pushes. The workflow's trailing `Persist state` step does the
same. Rationale: a silent standby or a yielding leader must only ever write
a **superset** — never wipe чужой прогресс.

## Duty protocol (leader ↔ standby, all via Contents API files)

- Claim: `leader_claim` (`run_once.py:625`) — check-then-PUT, last-writer-wins
  + one retry on sha race. New run claims `ready:false`, then `ready:true`
  only after Telegram connect succeeds (a run that can't reach Telegram
  must never look like a leader).
- Newcomer vs live leader → `_run_standby_loop` (`run_once.py:2009`): no
  Telegram, only GETs the flag every 30s; single shared `standby.json`.
  Older standby yields to a newer one ("newer wins" — the promoter is
  normally ≤31 min old, so inherited reigns stay long).
- `leader_should_yield` (`run_once.py:254`): yield only to a live + ready +
  strictly newer leader; equal start time → bigger `run_id` wins.
- `standby_should_promote` (`run_once.py:519`): missing flag, stale heartbeat,
  or `retiring` leader — confirmed over `PROMOTE_CONFIRM_READS` consecutive
  reads (no flapping on one slow GET). A leader near its deadline writes
  `retiring` + pushes → handoff is instant.
- Wedge takeover: heartbeat answers "process alive", `work.json` answers
  "work moves" (bumped every iteration + between chunks). Heartbeat fresh
  but the leader's own marker older than `WORK_STALE_SEC` → takeover after
  the same 2 confirming reads. No marker (old code) / foreign `run_id` /
  future stamp → never take over on that basis.
- Deadline inheritance (`leader_watch_seconds`): a promoted standby leads
  for the REMAINDER of its own watch, never a fresh full watch — otherwise
  the new deadline outlives the job and the rotation dies by platform kill
  (observed 2026-10-05: two healthy leaders killed mid-loop). Remainder ≤0
  → one useful iteration, then `retiring`.
- Split-brain resolution is always "newer wins"; duplicates are absorbed by
  the inbox claim-check + per-key due dedup.

## State merge — the one invariant that matters

`merge_states(local, remote)` (`run_once.py:675`) is a **union**, never an
intersection: `completed` keeps the fresher date per key, counters take
`max()` (incl. `google_key_cursor` — a local value must never roll the
rotation back), `last_run_utc` takes max. `prune_state` (`run_once.py:93`)
drops counters older than 3 days. Consequence: under a single writer,
pushed content is monotonically non-decreasing in keys.

`push_state_best_effort` (`run_once.py:723`): GET → merge → PUT → on sha
conflict one re-GET/merge/PUT. Then `_verify_push` (`run_once.py:748`):
re-read the branch, confirm all local `completed` keys are present;
on mismatch one more PUT + loud `🚨` log (tripwire, see Incidents §4).
A run never exits nonzero over state trouble — worst case the trailing
`Persist state` workflow step carries the local file.
Самодиагностика пропусков: новый лидер читает последний чужой heartbeat
(лидер+standby, свой run_id исключён) ДО claim'а; тишина дольше `GAP_WARN_SEC`
(дефолт 240с, env; временно снижен с 420, чтобы видеть частоту аварийных
зазоров 5–7 мин) — варнинг с длительностью в General (`_notify_inbox`, topic 1).
Маркер прогресса (`work.json` {run_id, last_work_utc}): heartbeat отвечает
«процесс жив», маркер — «работа движется». Дёргается раз в итерацию лидера
и между чанками (`PROGRESS_HOOK` из `create_summary`), публикуется ритмом
heartbeat (120с). Standby свергает живого по heartbeat лидера, если маркер
его же run_id протух дольше `WORK_STALE_SEC` (2 подтверждающих чтения).
Нет маркера (старый код) / чужой run_id — не свергаем.
Маяк дежурства в General (сессия 20): одно сообщение, две строки —
`heartbeat DD.MM HH:MM` + `next leader: DD.MM HH:MM`, всё МСК
(`format_liveness_text`, pure; без конца вахты — одна строка).
Новый лидер при заступлении подхватывает последний маяк поиском
(`liveness_find`: max id среди своих сообщений с префиксом, глубина
`LIVENESS_SCAN_LIMIT`) и правит его каждый `LIVENESS_EVERY`-й тик heartbeat;
нечего подхватить / правка упала (удалён вручную) — один перепост.
`msg_id` только in-memory (`_liveness_msg_id`; в `me` не кладём, иначе
утечёт во флаг `leader.json`). Ожидаемый конец вахты считает лидерский цикл
из своего дедлайна (стена = now + остаток; in-memory `_liveness_next_utc`) —
это ориентир передачи, не гарантия: при дропах тиков фактический преемник
встанет позже. Standby ничего не пишет (нет Telegram),
на время дыры маяк честно протухает. Пин ставит владелец вручную.
Для inbox безвреден (`parse` → None → `skip`).

## Load-bearing numbers (Actions mode only — VPS timing is just the APScheduler clock)

| Symbol | Value | Meaning |
|---|---|---|
| cron / timeout | `*/31`, 355 min | schedule drift; 330 watch + 15 tail + 1 setup (platform max 360) |
| watch / poll defaults | 19800s / 30s | duty length / inbox latency while leader lives |
| `LEADER_HEARTBEAT_SEC` / `LEADER_STALE_SEC` | 120 / 300 | liveness bound ≈ 2 missed beats |
| `WORK_STALE_SEC` (`work.json`) | 900 | wedge bound: fresh heartbeat + no progress 15 min → takeover |
| `SCHEDULE_REFRESH_SEC` | 600 | schedule staleness inside a 5.5h run |
| `PROMOTE_CONFIRM_READS` | 2 | anti-flap on promotion |
| `INBOX_MAX_ATTEMPTS` | 5 | command retries before deletion |
| `DUE_FAIL_THRESHOLD` / `DUE_FAIL_SKIP_SEC` | 3 / 3600 | скип падающего слота 1ч, возобновляемый: окно меряется от `last_utc`, по истечении — новый эпизод 3 попытки + 1ч тишины (`due_fail_record`, сессии 22/25) |
| `MAX_DUE_PER_ITERATION` | 1 | inbox-first latency bound |
| `LIVENESS_EVERY` / `LIVENESS_SCAN_LIMIT` | 5 / 50 | маяк в General: правка каждый 5-й тик (120с × 5 = 10 мин) / глубина подхвата |

## Incidents that shaped the design (don't re-learn)

1. LAG-window catch-up dropped slots when cron gaps exceeded the window →
   per-key `completed` dedup replaced windows entirely (`LAG_MAX`,
   `_last_run_msk_naive` deleted).
2. `timeout-minutes: 12` killed a run mid-analysis; command survived,
    un-deleted → looked like a "repeat". Timeout is 355 now (watch 330 +
    tail 15 + setup 1 = 346 ≤ 355); undone work
    retries via `inbox_attempts`, not via restarts.
3. Heartbeat sent once per N iterations froze during long summaries →
   leader looked dead mid-task. Heartbeat is a separate asyncio task now.
4. Due-drain storm (all unmarked slots in a row, tens of minutes) starved a
   valid inbox command → inbox-first + `MAX_DUE_PER_ITERATION=1`.
5. **CLOSED as one-off (2026-10-04 → 2026-10-05):** E2E run logged 5× `✅ выполнено, записано в state`
    (save runs strictly before that print) yet branch pushes + final Persist
    contained only 1 mark; no errors, single writer, all write paths
    union-safe. Static analysis found no loss path; live re-test with current
    code persisted 6/6 marks. Residual hypothesis: transient Contents-API
     inconsistency on rapid successive PUTs. Tripwire: `_verify_push` + `🚨`.
     Repro harness (ephemeral, NOT in repo): `/tmp/opencode/repro/replay.py`.
     No recurrence as of 2026-10-05 (all morning slots marked).
6. **Одноразовый backoff + шторм смены ключа (2026-10-05→06):** правка
   `SCHEDULE.txt` `1d`→`1d+` меняет ключ задачи (суффикс входит в ключ) —
   новый ключ без метки догоняется тем же вечером, даже если слот утренний
   (`compute_due`: опоздание любой длины догоняется). 4 «новых» слота +
   исчерпанная free-tier квота Gemini (429) дали 46 провалов одного слота
   за ночь тремя лидерами. Тогда `due_fails.first_utc` не обновлялся — скип 3ч
   срабатывал один раз, дальше повторы каждые ~8 мин до успеха. С тех пор
   скип возобновляемый (окно от `last_utc`, сессии 22/25) и укорочен до 1ч:
   ритм сухого слота теперь «3 попытки → 1ч тишины → …». Урок:
   смену периода/суффикса уже отработанного слота делать осознанно
   (это плановый перезапуск, не баг); тишину при исчерпанной квоте держит
   возобновляемый скип, успех не обязателен.

## Do-not-touch / discipline

- Secrets: `private.txt`, `telegram_session.txt`, `*.session` — gitignored,
  live in GitHub Secrets + locally only. Never commit, never print.
- `docs/CONTEXT_HANDOFF.md` = the *now* (live run ids, today's observations);
  stable knowledge lives HERE, not there. One source of truth each.
- Tests: `./venv/bin/python tests/test_run_once.py` must stay green (241 PASS as of
  2026-10-06; incl. `test_duty`, `test_heartbeat_loop`, `test_due_cap`,
  `test_inbox_retry_flow`, `test_duty_gap`, `test_work_wedge`,
  `test_leader_watch_inherit`, `test_timeout_budget`, `test_liveness`,
  `test_due_fail_renew`, `test_quota_diag`, `test_flat_rotation`,
  `test_503_holds_key`). The last one parses
  `summarize.yml` with plain regex (no new deps) and asserts
  watch + tail + setup ≤ timeout — the exact inequality that killed three
  leaders on 2026-10-05.
