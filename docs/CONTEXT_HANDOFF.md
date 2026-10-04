CONTEXT_HANDOFF — контекст последней сессии
Дисциплина: это единственный файл с «сейчас». Обновил состояние — обнови дату ниже. Закончил сессию — допиши итог в «Последний результат» и скорректируй «Дальше». Стабильное знание (числа-гейты, решения, источники) живёт в docs/_.md и сюда не дублируется.

Обновлено: 2026-10-04 (МСК)

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