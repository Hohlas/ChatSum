# ChatSum — Algorithm Description (agent onboarding)

Telegram summarizer bot, one codebase, **two runtime modes — use exactly one
at a time** (two live processes on one Telegram session desync updates and can
kill the authorization; README §"Выбор площадки"). Repo: `Hohlas/ChatSum`. Read this file
first; it replaces reading the whole code. Numbers below are load-bearing —
do not "tune" them without the owner. Locations are symbol names, never line
numbers (`grep -n "def merge_states" run_once.py`) — line numbers rot on
every edit.

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
   (all keys refused), never per rotation. 503/UNAVAILABLE: ONE request on
   the SAME key, then a 3-min pause (`SERVER_OVERLOAD_PAUSE_SEC=180`) and the
   call fails outward — NO internal retry, NO key walk. The repeat is the outer
   loop (inbox «Попытка N/5» / due «провал N»), so one request per attempt.
   Why: (1) the storm is backend-wide — the next key hits the same wall;
   (2) a 503 is charged against the daily limit — чужой замер через дашборд
   AI Studio: 31 из 50 засчитанных запросов были 503 «The model is overloaded»
   («Every time you send a request and receive a 503 response, Google records
   it as successful and counts your quota», dev forum Nov 2025; no official
   line in the docs — secondary data, but two independent observations plus
   the «503 lands AFTER key authentication» mechanics agree — do NOT redesign
   back into key-walking); (3) official guidance for 503 is «backoff and retry
   with a long maximum delay» (Gemini team reply, dev forum Feb 2026) —
   waiting is literally what is asked. Reduced from 3 tries/1+3 min
   (session 26) — each 503 burns the daily 20, and the outer inbox loop
   retries the command ×5, so one command could burn ~15 requests. Sustained
   503 also stops remaining chunks (`stop_due_to_overload`, mirror of the
   quota stop), so a multi-chunk summary doesn't sleep 3 min per chunk.
    500/502/504 stay on the old short-retry path (second-long blips).
    Timeouts and connection breaks ride the same short path (retries with
    10/20s pauses on the same key, then rotation) but with a long fuse:
    HTTP budget 600s — `gemini-3.8-flash` + thinking high answers non-streamed,
    one request waits for the full thought (session 27: 180s cut healthy
    answers). SDK retries are OFF (`max_retries=0`) — retries are ours +
    rotation only, never layered (library × ours multiplies). Detection:
    `_is_timeout_text` catches both `timeout` and `timed out` (the latter is
    what the library actually emits); `_is_connection_error` matches by TYPE
    (`APIConnectionError`, wraps `httpx.ReadError`/`ConnectError` — the text
    is a bare `Connection error.` and must not be trusted). A break before
    the answer almost surely spends no quota, so retry+rotate is cheap.
    Observability per attempt: `⏱️ Попытка:` log line (key, retry, error type,
    seconds), error type in the TG text, full traceback to stdout; TG hints
    for timeout ("model thought past the 600s budget, outer loop repeats")
    and for connection break.
   TG 429 line carries quotaId + retry (`format_quota_diagnostic`; retry also
   parsed from message text 'Please retry in 17h1m40s'). TG 503 line is the
   **verbatim** Google error text (`format_server_overload_diagnostic`, `❌`
   prefix only); its response headers are dumped to the log
   (`_log_response_headers`) to look for an overload-end signal.
- `run_once.py` — scheduler + duty wrapper. Imports `main.py`. Entry points:
  `--due` (manual drain), `--watch` (duty loop). All times in slots are
  **MSK = UTC+3** (`MSK` in `run_once.py`).
- `SCHEDULE.txt` (on `main`) — slots `chat_id|HH:MM|limit`, one per line.
  Leading `+` is stripped. Limit `10` = cheap probe, `1d` = full summary.
- `.github/workflows/summarize.yml` — **только ручной запуск** (`workflow_dispatch`).
  Расписание отключено 2026-10-20 решением владельца: круглосуточное дежурство
  на GitHub-hosted раннере расходится с GitHub Terms for Additional Products and
  Features, §Actions (редакция 27.08.2026): «You may only access and use GitHub
  Actions to develop and test your application(s)», «don't use Actions … as part
  of a serverless application», «burden … disproportionate to the benefits».
  Крон-блок законсервирован комментарием в самом файле: включение обратно =
  раскомментировать `schedule` (минуты `7,19,33,46,58` выбраны мимо пика
  :00–:05 из-за задержек/дропов по докам; зазоры 12–14 мин + шов 9 мин) и
  вернуть длину вахты, таймаут и `GAP_WARN_SEC`.
  `timeout-minutes: 150` (вотч 120 + хвост 15 + установка 1 = 136 ≤ 150;
  запас 14 мин). **no `concurrency`** (перекрытие — часть протокола: второй
  ран уходит в standby, а не ждёт в очереди).
  Входы: `chat_id`/`period` (разовый прогон конкретного чата), `watch_seconds`
  (default 7200 = 2 ч, он же дефолт кода), `poll_interval` (30), `drain_all`
  (default true → первая итерация выполняет ВСЕ накопившиеся слоты, дальше
  обычный кап; в ручном режиме слоты копятся между прогонами).
  `GAP_WARN_SEC=604800` в env — сторож пропусков молчит: в ручном режиме пауза
  между прогонами норма, а не авария. Stdout unbuffered (`PYTHONUNBUFFERED=1`),
  чтобы веб-логи шли в реальном времени (сессия 27: лог отставал на ~5 мин).
- `state` branch (GitHub Contents API, `STATE_DEFAULT` in `run_once.py`):
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

Entry: `python3 main.py` → `main()` in `main.py` → Telethon client
`run_until_disconnected()`. One process holds one Telegram session
(`session_name.session` file; `telegram_session.txt` holds the StringSession
copy). Config is local `private.txt` (+ `PROMPT.txt`, `EXCLUDED_USERS.txt`,
`PRIORITY_USERS.txt`, `SCHEDULE.txt` from the working tree).

- Commands are **outgoing slash-commands from the owner's own account**
  (handler on `events.NewMessage(outgoing=True, ...)` in `main.py`): `/sum` (`3h` / `45` / `600-800` / `2d-3d` / `1d+` — the
  `+` also posts the result back into the source chat), `/copy` (same ranges,
  no AI = no API cost), `/config` family (`/show_model`, `/set_model`,
  `/add_excluded`, `/reload_config`, …), `/sch HH:MM period` + `/sch_list` +
  `/unsch`, `/help`. Send `/sum …` in any chat, the userbot answers there.
- Schedule: APScheduler `AsyncIOScheduler` in `main.py`; `reload_schedule()`
  registers one `CronTrigger` per slot in MSK
  (`scheduled_analysis_job` → `run_analysis(…, scheduled=True)`).
- No dedup layer: a trigger fires once by the clock. If the process is down
  at fire time, **that slot is silently missed** — no catch-up (this exact
  weakness is why the Actions mode uses per-key `completed` marks instead).
- Live management (`/set_model`, `/reload_config`, `/sch` edits) exists ONLY
  here. In Actions mode these commands do not exist; model comes from
  `GEMINI_*` repo Variables, schedule only via git commits to `SCHEDULE.txt`.

## GitHub Actions mode (manual duty, current production mode)

No long-lived process: a manual workflow run (`Run workflow` in the UI) works
as leader for `watch_seconds` (default 2h) and exits; nothing restarts it.
Between runs the bot is silent — that is the intended state (решение владельца
2026-10-20, причины в §Components). Если одновременно поднять второй ран, он
уйдёт в standby (Telegram не трогает) и будет держать машину до своего дедлайна.

Протокол лидер/standby, handoff через ветку `state`, промоушен и наследование
дедлайна остались как были — они работают и внутри одного ручного рана
(standby подхватывает, если лидер умер), и при случайном перекрытии ранов.

## Leader iteration (the hot loop, `_run_leader_loop` in `run_once.py`)

Each iteration, strictly in this order:

1. **Inbox first** — `poll_inbox_first` in `run_once.py`: deep first pass
with `offset_id` pagination (10×100) so commands buried under summaries
during a gap are not skipped. Processing via `_process_batch` /
`process_inbox_message`. Before starting an
   analysis the message is re-read (claim-check vs double-take by a rival).
   Failure does NOT delete the command: `inbox_attempts` counter up to
   `INBOX_MAX_ATTEMPTS=5` with "🔁 Попытка N/5" progress; `last_seen` rolls
   back to (oldest retry − 1) so retry comes in ~30s, not after restart.
   Only success or exhaustion deletes the command.
2. **At most one due task** — `run_due_once(..., max_tasks=1)`
    (`MAX_DUE_PER_ITERATION`; manual `--due` passes
    None = drain all). Per-key dedup: `compute_due(entries, now, completed)`
    — a slot fires once per key `chat|HH:MM|limit` per
    occurrence date. After 3 consecutive failures a slot is skipped for 10 min
    (`due_skip_info`; quota protection). Success clears.
    Worst inbox delay ≈ one summary duration.
3. Background: `_heartbeat_loop` (own asyncio task)
    re-claims + pushes every `LEADER_HEARTBEAT_SEC` (interleaves on Gemini/
    Telegram awaits — a long summary must NOT look like a dead leader).
    State + work-marker push ride the same 120s rhythm,
    `SCHEDULE.txt` refresh from `origin/main` every
    `SCHEDULE_REFRESH_SEC` (a 2h run would otherwise go stale).

Cleanup (`_watch_cleanup`) ALWAYS pull-merges first,
then saves/pushes. The workflow's trailing `Persist state` step does the
same. Rationale: a silent standby or a yielding leader must only ever write
a **superset** — never wipe чужой прогресс.

## Duty protocol (leader ↔ standby, all via Contents API files)

- Claim: `leader_claim` — check-then-PUT, last-writer-wins
  + one retry on sha race. New run claims `ready:false`, then `ready:true`
  only after Telegram connect succeeds (a run that can't reach Telegram
  must never look like a leader).
- Newcomer vs live leader → `_run_standby_loop`: no
  Telegram, only GETs the flag every 30s; single shared `standby.json`.
  Older standby yields to a newer one ("newer wins" — the promoter is
  normally ≤14 min old, so inherited reigns stay long).
- `leader_should_yield`: yield only to a live + ready +
  strictly newer leader; equal start time → bigger `run_id` wins.
- `standby_should_promote`: missing flag, stale heartbeat,
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

`merge_states(local, remote)` is a **union**, never an
intersection: `completed` keeps the fresher date per key, counters take
`max()` (incl. `google_key_cursor` — a local value must never roll the
rotation back), `last_run_utc` takes max. `prune_state`
drops counters older than 3 days. Consequence: under a single writer,
pushed content is monotonically non-decreasing in keys.

`push_state_best_effort`: GET → merge → PUT → on sha
conflict one re-GET/merge/PUT. Then `_verify_push`:
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
это ориентир передачи, не гарантия: в ручном режиме автоматического преемника
нет, и маяк честно протухает до следующего запуска. Standby ничего не пишет (нет Telegram),
на время дыры маяк честно протухает. Пин ставит владелец вручную.
Для inbox безвреден (`parse` → None → `skip`).

## Load-bearing numbers (Actions mode only — VPS timing is just the APScheduler clock)

| Symbol | Value | Meaning |
|---|---|---|
| watch / timeout | 7200 s / 150 min | длина вахты ручного прогона (120 + 15 хвост + 1 установка = 136 ≤ 150) |
| poll default | 30 s | inbox latency while the leader lives |
| `drain_all` (workflow input) | true | первая итерация берёт ВСЕ накопившиеся слоты (`due_max_tasks` → None), дальше кап |
| `LEADER_HEARTBEAT_SEC` / `LEADER_STALE_SEC` | 120 / 300 | liveness bound ≈ 2 missed beats |
| `WORK_STALE_SEC` (`work.json`) | 900 | wedge bound: fresh heartbeat + no progress 15 min → takeover |
| `SCHEDULE_REFRESH_SEC` | 600 | schedule staleness inside a 2h run |
| `PROMOTE_CONFIRM_READS` | 2 | anti-flap on promotion |
| `INBOX_MAX_ATTEMPTS` | 5 | command retries before deletion |
| `DUE_FAIL_THRESHOLD` / `DUE_FAIL_SKIP_SEC` | 3 / 600 | скип падающего слота 10 мин, возобновляемый: окно меряется от `last_utc`, по истечении — новый эпизод 3 попытки + 10 мин тишины (`due_fail_record`, сессии 22/25; 10 мин — решение владельца 2026-10-11) |
| `MAX_DUE_PER_ITERATION` | 1 | inbox-first latency bound (снят на первой итерации при `drain_all`) |
| `LIVENESS_EVERY` / `LIVENESS_SCAN_LIMIT` | 5 / 50 | маяк в General: правка каждый 5-й тик (120с × 5 = 10 мин) / глубина подхвата |

## Incidents that shaped the design (don't re-learn)

1. LAG-window catch-up dropped slots when cron gaps exceeded the window →
   per-key `completed` dedup replaced windows entirely (`LAG_MAX`,
   `_last_run_msk_naive` deleted). Тот же механизм держит ручной режим: опоздание
   любой длины догоняется, отметка — на дату последнего наступления.
2. `timeout-minutes: 12` killed a run mid-analysis; command survived,
    un-deleted → looked like a "repeat". Бюджет считается по формуле
    «вотч + хвост 15 + установка 1 ≤ таймаут» (`test_timeout_budget` парсит yml);
    сейчас 120 + 15 + 1 = 136 ≤ 150, до 2026-10-20 было 330 + 15 + 1 ≤ 355.
    Undone work retries via `inbox_attempts`, not via restarts.
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
   скип возобновляемый (окно от `last_utc`, сессии 22/25) и укорочен до 1ч,
   2026-10-11 — до 10 мин по решению владельца:
   ритм сухого слота теперь «3 попытки → 10 мин тишины → …». Урок:
   смену периода/суффикса уже отработанного слота делать осознанно
   (это плановый перезапуск, не баг); тишину при исчерпанной квоте держит
   возобновляемый скип, успех не обязателен.

## Do-not-touch / discipline

- Secrets: `private.txt`, `telegram_session.txt`, `*.session` — gitignored,
  live in GitHub Secrets + locally only. Never commit, never print.
- `docs/CONTEXT_HANDOFF.md` = the *now* (live run ids, today's observations);
  stable knowledge lives HERE, not there. One source of truth each.
- Tests: `./venv/bin/python tests/test_run_once.py` must stay green (**467 PASS,
  0 FAIL, as of 2026-10-20**; прежняя запись «390 PASS + 1 KNOWN fail
  `nemotron: чанк 100K`» устарела — тот тест проходит). Включает `test_duty`,
  `test_heartbeat_loop`, `test_due_cap`, `test_due_drain_on_start`,
  `test_inbox_retry_flow`, `test_duty_gap`, `test_work_wedge`,
  `test_leader_watch_inherit`, `test_timeout_budget`, `test_liveness`,
  `test_due_fail_renew`, `test_quota_diag`, `test_flat_rotation`,
  `test_503_holds_key`, `test_server_overload_diag`,
  `test_connection_error_retries`). `test_timeout_budget` parses
  `summarize.yml` with plain regex (no new deps) and asserts
  watch + tail + setup ≤ timeout — the exact inequality that killed three
  leaders on 2026-10-05.
