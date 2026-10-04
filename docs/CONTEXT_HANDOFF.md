CONTEXT_HANDOFF — контекст последней сессии
Дисциплина: это единственный файл с «сейчас». Обновил состояние — обнови дату ниже. Закончил сессию — допиши итог в «Последний результат» и скорректируй «Дальше». Стабильное знание (числа-гейты, решения, источники) живёт в docs/AlgDescription.md и сюда не дублируется.

Обновлено: 2026-10-04 (МСК), сессия 3 — standby + ретраи + per-key (НЕ закоммичено)

## Задача сессии
Владелец предложил: крон 15 мин + часовой цикл с перекрытием и передачей флага через общее хранилище; цель — ожидание команд не более минуты, только GitHub Actions.

## Задача сессии
Владелец сообщил: (1) не сработали расписания (06:00 и др.), (2) не срабатывают команды из General и топиков канала результатов.

## Ключевая модель работы (важно для продолжения)
- Расписание и inbox работают НЕ через долгоживущий процесс VPS, а через GitHub Actions:
  `.github/workflows/summarize.yml`, cron `*/10 * * * *` (UTC), шаг `run_once.py --watch --poll-interval 60 --watch-seconds 480`.
- `run_once.py` импортирует `main.py` (общее ядро). due-задачи → `main.scheduled_analysis_job`; inbox → `main.run_analysis`.
- Дедуп в ветке `state` (`state.json`, поля `completed`, `last_run_utc`, `google_key_cursor`).
- `SCHEDULE.txt` в main. Даты/слоты — МСК (`MSK = UTC+3`, `run_once.py:33`).
- Секреты в GitHub Secrets, не в репо: `private.txt`/`telegram_session.txt` в `.gitignore` (строки 211–212). `private.txt` локально есть, `SCHEDULE.txt` — трекается.

## Диагноз причин (подтверждено логами)
1. Расписания. Окно «должных» было `[now−15 мин, now]` (`LAG_MAX_DEFAULT=15`). Cron GitHub — best-effort: между ранами реально бывали часы (напр. ран 2026-10-03T23:58Z = 06:58 МСК; до него — 21:24Z). Слот, попавший в зазор > 15 мин, навсегда выпадал: следующий ран видел occurrence вне окна. Отсюда пропуски 06:00–07:15 МСК.
2. Команды из General/топиков. `process_inbox_message` для `sum`/`copy` требовал ссылку `t.me/...`, @username или известный топик. Команда без ссылки в General удалялась с ошибкой. `sch`/`unsch` без аргументов работают как список/снятие (это не баг). Оба теста канала (`sum2d` top=3446, `sch` General) не обрабатывались, т.к. стояли между ранами.
3. Ночью ран не стартовал вообще (последний ран 23:58Z, следующий ~06:42Z) — заполнение слотов 06:00–07:15 пришлось на окно без рана.

## Сделано в этой сессии (УЖЕ В КОММИТЕ ad1fd1a, на origin/main)
- `LAG_MAX_DEFAULT = 45` (`run_once.py:35`).
- Догон простоя: `compute_due(..., since_msk_naive=None)` расширяет окно до времени последнего успешного рана (`run_once.py:199`); хелпер `_last_run_msk_naive` (`run_once.py:224`); `run_due_once` передаёт `since_naive` (`run_once.py:323`).
- Резолв источника из General по названию чата: `resolve_name_source` (`run_once.py:509`), подключён в `_sched_add` (`:727`) и в обработку `sum`/`copy` (`:868`). Исключает сам командный чат, названия от длинных к коротким, минимум 3 символа.
- README обновлён (разделы про inbox и окно). docs/GitHubActions.md и docs/audit.md перенесены из plans/.
- Тесты: `test_run_once.py` — добавлены кейсы догона и резолва по названию. `./venv/bin/python test_run_once.py` → ALL TESTS PASSED.

## Статус коммита
- Коммит `ad1fd1a` («feat: enhance scheduling logic and inbox command handling») УЖЕ на `origin/main` (HEAD == origin/main). Фикс расписания/inbox в проде.
- docs/CONTEXT_HANDOFF.md — единственное незакоммиченное изменение (владелец просил не коммитить).

## Сессия 2 — дежурная схема с handoff (код готов, НЕ коммитить без команды)
- Решение владельца (уточнено в сессии): крон `7 * * * *` (раз в час, минута 7 — вдали от начала часа), watch 5 ч (18000 с), poll 30 с, флаг `leader.json` в ветке `state`.
- Честная оговорка: гарантию «≤1 мин любой ценой» чистый Actions дать не может — зазоры cron 2.5–5.5 ч (21:24→23:58→05:28Z 03–04.10) и убийство рана по timeout остаются. Схема даёт ~30–60 сек, ПОКА жив дежурный; смерть дежурного при кроне */60 = до ~часа без покрытия. Если захочется быстрее восстанавливаться — вернуть `*/15` (4× холодных стартов, бесплатно на public).
- `run_once.py`: блок handoff — `new_leader_doc` / `leader_is_live` / `leader_should_yield` (уступаем только живому ready более новому) / Contents API `gh_state_file_get/put` / `leader_claim` (last-writer-wins + ретрай) / `merge_completed` / `pull_state_best_effort` / `push_state_best_effort` / `refresh_schedule_best_effort`; `run_watch` переписан (claim неготовым → ready после connect → пауза poll → проверка вытеснения; в цикле проверка флага каждую итерацию, heartbeat+push state каждые 300 с, refresh SCHEDULE каждые 600 с); claim-check в `process_inbox_message` (перечитать msg перед анализом — защита от двойного взятия); push state после каждой due-задачи; дефолты 18000/30.
- `summarize.yml`: cron `7 * * * *`, `timeout-minutes: 330`, **concurrency убран** (перекрытие — суть схемы), inputs/шелл-дефолты 30/18000, env `GITHUB_TOKEN` + `GITHUB_REPOSITORY` для Contents API.
- `test_run_once.py`: `test_handoff` (9 кейсов) + `watch defaults` обновлён → ALL TESTS PASSED, YAML валиден.
- Не закоммичено (по просьбе владельца): `run_once.py`, `summarize.yml`, `test_run_once.py`, этот файл. `docs/audit.md` изменён до сессии — не трогал.
- Дальше (E2E): ручной `workflow_dispatch` с watch_seconds=600/poll=10 → в логе «👑 Handoff включён», второй запущенный ран забирает флаг, первый graceful-выходит; `leader.json` в ветке `state`; команда `sum...` в General выполняется за ~30–60 сек; due-догон через `since` как раньше.

## Сессия 3 — standby + ретраи + per-key метки (код готов, НЕ коммитить без команды)
- Диагноз инцидента 8:29 МСК (05:29 UTC): ран 05:28:43Z забрал команду за минуту (опрос работает), написал «Начинаю» + «Большой объём, 6 этапов» (`main.py:3014`) и был убит `timeout-minutes: 12` посреди 6 вызовов Gemini. Команда не удалена → пережила смерть → повтор следующим дежурным. Таймаут 330 эту причину закрывает.
- Дыра 1 закрыта: провал анализа (`False`/исключение) больше не удаляет команду — счётчик `state['inbox_attempts']` до `INBOX_MAX_ATTEMPTS=5`, прогресс «🔁 Попытка N/5», удаление только на исчерпании; `last_seen` откатывается к (старейший retry − 1) — повтор через ~30 сек, а не после рестарта; `True` в `main.py:3460` уже означает «все send_* прошли» (флаг в самом конце). Возврат `process_inbox_message`: добавлен `'retry'` (в `failed_total` не входит).
- Дыра 2 закрыта: `compute_due(entries, now, completed)` — per-key метка вместо окна; `--lag-max`/`LAG_MAX_DEFAULT`/`_last_run_msk_naive` удалены; `due_fails` + `due_skip_info` — пропуск на 3 ч после 3 подряд провалов (защита квоты); успех сбрасывает.
- Standby (предложение владельца, реализовано в Actions-совместимой форме): новичок при живом лидере идёт в `_run_standby_loop` (без Telegram, только GET флага/30 с), флаг `standby.json` один (вытеснение «новее побеждает»); promotion при пропавшем/протухшем 2 подряд чтения/retiring лидере; лидер на дедлайне пишет `retiring` + push → передача мгновенная; сплит-брейин разруливается «новее побеждает». Heartbeat 120 с / stale 300 с; tie-break равного старта по большему `run_id`.
- Анти-затирание state: `_watch_cleanup` всегда pull-merge (union `merge_states`: completed свежая дата + счётчики max + last_run max) перед save — молчаливый standby/уходящий лидер пишут надмножество; `Persist state` в workflow безопасен. `prune_state` чистит счётчики старше 3 суток.
- Глубокий первый проход `poll_inbox_first`: пагинация `offset_id` до 10×100 — команда, заваленная саммари за 5-часовой зазор, не пропускается.
- `summarize.yml`: cron `*/11 * * * *` (11 взаимно просто с 60 — дрейф мимо пика :00). README обновлён (окно LAG → per-key, ретраи, дежурство).
- Тесты: 144 PASS incl. новые `test_duty` (promote/backoff/попытки/merge/prune) и `test_inbox_retry_flow` (retry→last_seen откат→успех/исчерпание); фейки доучены до `offset_id`/`get_messages`. YAML валиден.
- Не закоммичено: `run_once.py`, `test_run_once.py`, `summarize.yml`, `README.md`, этот файл. ЧУЖОЕ НЕ ТРОГАЛ: `D docs/GitHubActions.md`, `M docs/audit.md`, `?? docs/AlgDescription.md` — изменения не мои (параллельная работа владельца?), оставить как есть.
- Дальше (E2E): 2 параллельных ручных рана → standby в логах + promotion при убийстве лидера (отмена рана вручную); команда при живом дежурном → ~минута; слот, проваленный при свежем `last_run_utc` → догон следующим циклом.

## Незакрытое / Дальше
1. Проверить E2E в Actions: cron-ран зелёный; в логе окно расширено до `since`; команда `sum2d` в General без ссылки резолвится по названию чата; слоты 06:00/06:15/06:50/06:55/07:15 МСК догоняются следующим раном.
2. Возможная чистовая правка: `watch_seconds` оставлен 480 (меньше cron 10 мин, чтобы раны не накладывались) — это осознанно, т.к. догон теперь через `since`, а не через длинный watch.
3. `docs/GitHubActions.md` §23–24 (стабильное знание) описывает LAG_MAX=15 — при желании синхронизировать с новым 45.
4. Не трогать: `session_name.session`, `private.txt`, `telegram_session.txt` — секреты (в .gitignore).

## Полезные команды
- Тесты: `./venv/bin/python test_run_once.py` (нужен `asyncio.set_event_loop(asyncio.new_event_loop())` только для `test_bot.py`; `test_run_once.py` самодостаточен).
- Список расписания: `./venv/bin/python run_once.py --list`.
- Логи Actions: `gh run list --workflow=summarize.yml`; `gh api repos/Hohlas/ChatSum/actions/jobs/<job_id>/logs`.
- Состояние: `git show origin/state:state.json`.

## Известные грабли (не баги, не «чинить»)
- `sch`/`unsch` без аргументов — это не команда расписания, а список/снятие.
- parse-команда `sum2d` возвращает `days=2` (обычный sum), не SCHED_RE.
- Если secret `TELEGRAM_API_ID` пуст, ран падает на preflight с exit 2 — это красные раны от 02–03.10 (до настройки секретов).
  

## Подтверждено офиц. доками GitHub:
- «The schedule event can be delayed during periods of high loads… High load times include the start of every hour. If the load is sufficiently high enough, some queued jobs may be dropped. To decrease the chance of delay, schedule your workflow to run at a different time of the hour.»
- Минимальный интервал — 5 минут. Scheduled workflows идут только с default branch. В public-репо отключаются после 60 дней без активности.  