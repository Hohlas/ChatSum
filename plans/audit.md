# Аудит: реализация плана plans/GitHubActions.md (вариант B)

Объект проверки: handoff-описание («Изменённые/Новые файлы») + артефакты `main.py`, `run_once.py`, `gen_session.py`, `.github/workflows/summarize.yml`, `test_run_once.py`, `requirements.txt`, `README.md`, `.gitignore` против плана `plans/GitHubActions.md`.

Окружение проверки: локальная машина (Linux), git 2.53, venv `/tmp/opencode/cs-venv` (Telethon 1.34.0, APScheduler 3.11.3, httpx 0.28.1, openai 3.23.0, python 3.14.4; workflow таргетит 3.11 — расхождение noted ниже). Все git-сценарии воспроизведены на bare-репозиториях в `/tmp/opencode/wf*`, `git-sim*`.

---

## 1. Итог по критериям

**Локальная корректность.** Большинство утверждений handoff и плана подтверждены кодом и командами. Реализация соответствует плану по структуре: `load_env_config`/`_config_errors_or_exit` (main.py:166,194), `build_session` со StringSession (main.py:703–711), возвраты `bool` из `run_analysis`/`scheduled_analysis_job`, `run_once.py` с `--due/--list/--chat-id`, тесты окна/дедупа/полночи/prune, workflow с cron `*/5` + workflow_dispatch + concurrency + `$RUNNER_TEMP` + `read-tree --empty` + rebase-ретраем. `apscheduler>=3.10.0` добавлен в `requirements.txt`. Модульные тесты `test_run_once.py` проходят все 8 кейсов (запуск ниже).

Однако найдены **две критические ошибки в workflow-логикеPersist/Fetch** и **один критический сбой приёмочной команды**, а также несколько «важных» несоответствий плану — см. раздел 2.

**Целостность.** Ядро варианта B (дедуп по `chat_id|HH:MM|period` с МСК-датой, запись state после каждого задания, код возврата) реализовано согласованно; `compute_due` даёт ровно одно срабатывание при пересечении полуночи (тест подтверждает), `task_key` совпадает с форматом 4.1 (с оговоркой — см. 2.6).

**Обоснованность подхода.** Выбор ветки `state` + `$RUNNER_TEMP`, `override=False` для dotenv, импорт-тайм валидация только для VPS — аргументированы в плане и корректны сами по себе. Риск «одна сессия одновременно» отражён в README и плане.

---

## 2. Замечания

### 2.1. КРИТИЧНО. Workflow: `git fetch origin state` не создаёт `origin/state` при default `fetch-depth` checkout@v4 → Persist на 2+ запуске падает.

Место: `.github/workflows/summarize.yml:36` («Fetch state») и `:78–83` («Persist state»); plan `GitHubActions.md:249` и раздел «Почему state.json вне дерева».

Суть: `actions/checkout@v4` по умолчанию делает `fetch-depth: 1` и настраивает `remote.origin.fetch` **только** на ref целевой ветки (`+refs/heads/main:refs/remotes/origin/main`) — это подтверждено в исходниках экшена (`src/ref-helper.ts:91–96` `getRefSpec` для `refs/heads/` возвращает `[+${ref}:refs/remotes/origin/${branch}]`, без wildcard). Локальная реплика ровно этих настроек (`/tmp/opencode/wf2/work5`, config `remote.origin.fetch='+refs/heads/main:refs/remotes/origin/main'`, `fetch --depth=1`): `git fetch origin state` завершился exit=0, создал **только `FETCH_HEAD`**, `git show-ref | grep state` пуст, а `git show origin/state:state.json` → `fatal: invalid object name 'origin/state'`.

Следствия по workflow:
- «Fetch state»: `git show origin/state:state.json 2>/dev/null || echo '{}'` всегда берёт ветку `{}` → **dedup не работает между запусками** (state каждый раз пустой, задачи повторяются каждые 5 минут). Это прямо противоречит цели варианта B.
- «Persist state»: `if git fetch origin state 2>/dev/null` истинен (fetch проходит в FETCH_HEAD), затем `git checkout -B state origin/state` → fatal (ref не существует) → шаг падает, state не пушится никогда. Симуляция второго запуска подтвердила отсутствие `refs/remotes/origin/state`.
- Первый запуск «повезёт» (fetch fail → orphan + push OK), что маскирует баг: первый run зелёный.

Доказательство: вывод симуляции в 1-м bash-блоке; поведение git-2.53 с `git fetch origin state` при single-branch refspec; документация actions/checkout v4 README («Only a single commit is fetched by default… Set `fetch-depth: 0` to fetch all history for all branches and tags»).

Почему важно: ломает центральный механизм (дедупликацию) и персист состояния; в репозитории уже есть ветка `state` (`git ls-remote origin state` → SHA есть), т.е. каждый плановый run после первого будет падать в Persist.

Рекомендация (минимальный дифф): в «Fetch state» использовать `git fetch --depth=1 origin refs/heads/state:refs/remotes/origin/state` (явный refspec создаёт remote-tracking ref), либо `git fetch origin +refs/heads/state:refs/remotes/origin/state`. Аналогично в Persist (`if git fetch --quiet origin refs/heads/state:refs/remotes/origin/state; then …`). Альтернатива: `actions/checkout@v4 with: fetch-depth: 0` (тяжело для public-cron */5). Оба варианта легко проверить локальной симуляцией (моя `work5` воспроизводит баг, `wf2/work` с wildcard-refspec — отсутствие бага).

### 2.2. КРИТИЧНО. Приёмочная команда плана не проходит: `TELEGRAM_SESSION=x python -c "import main"` → ValueError.

Место: `GitHubActions.md:567` («Обязательные проверки») и `run_once.py:140–148`; реализация `main.py:703–711`.

Суть: `build_session()` вызывает `StringSession('x')`, Telethon кидает `ValueError: Not a valid string`. Прогон (venv): `TELEGRAM_SESSION=x TELEGRAM_API_ID=1 TELEGRAM_API_HASH=h python -c "import main"` → падает именно на импорте. То есть критерий приёмки «`import main` безопасен при `TELEGRAM_SESSION=x`» **не выполнен** — план сам задаёт недопустимое значение («x» не валидная StringSession; у Telethon кодирование требует корректной структуры, пустая `StringSession().save()` == `''`).

Замечание двустороннее:
- Если цель теста — «импорт не падает на Actions», нужен валидный токен. `run_once.py` это учитывает (`preflight_import_env` конструирует StringSession до импорта и exit(2) при битом токене; подтверждено: `TELEGRAM_SESSION=garbage run_once.py --list` → понятный exit=2, без интерактива). Для голого `import main` без обходного пути — тест плана невалиден.
- На VPS-маршруте (session=='session_name') проблемы нет —Telethon сам создаёт файловую сессию.

Рекомендация: в плане заменить фиктивное `TELEGRAM_SESSION=x` на «валидный StringSession из `gen_session.py`» или на `TELEGRAM_SESSION=`(пусто) с оговоркой, что пусто ⇒ VPS-маршрут (см. 2.3), либо тестировать безопасность импорта через `run_once.py --list` (что и делается в README). Иначе приёмка формально провалится.

### 2.3. ВАЖНО. Пустой секрет TELEGRAM_SESSION переводит раннер на VPS-маршрут: создаётся private.txt из шаблона и (в ранней фазе) возможна интерактивная авторизация.

Место: `main.py:158–160,220–221` (условие `if not os.getenv('TELEGRAM_SESSION')`), `run_once.py:140–149,152–164`.

Суть: в Actions при незаполненном/отсутствующем секрете `${{ secrets.TELEGRAM_SESSION }}` раскрывается в **пустую строку**, `os.getenv` даёт `''` → falsy. Тогда на импорте отрабатывает `ensure_private_file()` (симуляция: `TELEGRAM_SESSION= run_once.py --due` вывело «✅ Создан файл private.txt из шаблона») и импорт-тайм `_config_errors_or_exit()`; дальше `preflight_import_env` пропускает пустую сессию (только непустую валидирует), `preflight_actions_env` ловит `TELEGRAM_SESSION` и exit(2) — хорошо, но **exit происходит после создания private.txt в рабочем дереве**. В плане 1.1 «при TELEGRAM_SESSION в env ensure_private_file() можно пропустить» реализовано как проверка `if not os.getenv(...)`, что корректно для непустого значения и некорректно для пустого.

Это не ломает dedup-логику, но: (а) на раннере появляется мусор-файл private.txt (в git не попадёт: `.gitignore:211` его игнорит — проверено diff), (б) в гипотетическом прогоне с заполненными GOOGLE/TELEGRAM секретами, но пустой сессией, `telegram_client.start()` может уйти в интерактив (план Фазы 3 шаг 6 «без интерактива» нарушен бы, но `preflight_actions_env` стоит до `start()` в обоих путях — run_once.py:308,317–319 — так что интерактив блокируется; зафиксировано).

Рекомендация: в preflight различать «пустой секрет» и «не задан» (`if 'TELEGRAM_SESSION' not in os.environ` — в Actions env всегда определён, просто пуст) и выдавать `❌ TELEGRAM_SESSION пуст (секрет не заполнен)` до импорта main; в `preflight_import_env` проверять непустоту сессии так же строго, как и API_ID.

### 2.4. ВАЖНО. `run_analysis` возвращает False для штатных «нет сообщений»/«всё отфильтровано»/«ошибка Gemini» — такие слоты никогда не помечаются выполненными → бесконечные повторы каждые 5 мин до конца окна LAG_MAX.

Место: `main.py:2894,2954,2969` (early `return False`), `run_once.py:236–238` (не пишет state при not True), plan `GitHubActions.md:198–199` (код возврата «не 0 — если хотя бы одна упала»).

Суть: контракт К2 в плане (4.4) трактует True как «после успешной публикации», False — «любое перехваченное исключение» («Сигнал достоверен: … False — при любом перехваченном исключении», строки 282–283). Реализация возвращает False **не только** при исключениях: при отсутствии сообщений за период, при полной фильтрации и при получении `❌`-summary из `create_summary` (`main.py:2964–2969`) — это штатные ситуации, а не crash. В Actions: чат без сообщений ⇒ задача «проваливается» ⇒ красный run + повтор на каждом cron-тике в течение окна, затем, когда окно пройдёт (15 мин), слот считается пропущенным до завтра.

Это противоречит заявлению в плане (False == только перехваченное исключение). Строка плана «сейчас их [ранних return] нет» (`GitHubActions.md:274`) фактически опровергнута диффом: `git show HEAD:main.py` содержит три `return` (без значения) на строках 2861, 2921, 2936 старого файла, и они преобразованы в `return False` — т.е. ранние выходы были и до рефакторинга.

Рекомендация: либо различать статусы (например, возвращать `'ok'/'empty'/'error'` или пару `(published: bool, hard_error: bool)`) и писать state при `empty` (слот «отработал», повторять нечего — код возврата run остаётся 0), либо явно документировать в плане, что пустые периоды не дедупицируются и требуют ручной реакции. Текущее поведение — вероятный источник ежедневного шума и красных RUN для чатов с редкими сообщениями. (Неподтверждённая часть: частота пустых периодов в реальных чатах — гипотеза; сам код-путь подтверждён.)

### 2.5. УЛУЧШЕНИЕ. `prune_state` несовместим с МСК-датами високосного/конца месяца? Нет; но сравнение строк ISO OK. Реальная проблема: prune удаляет «старые» дедуп-записи ровно на пороге 3 суток — безвредно, но тест `prune` проверяет only 4-дневную метку. Место: `run_once.py:79–85`, `test_run_once.py:87–93`. Существенного багa нет — фиксирую как мелкое улучшение покрытия.

### 2.6. УЛУЧШЕНИЕ. Формат ключа в плане vs реализация.

Место: `GitHubActions.md:240` (`period{suffixes}`) vs `run_once.py:88–95` (суффиксы `+`/`-` конкатенируются в порядке `+` затем `-`). `SCHEDULE.txt` и `load_schedule._parse_suffixes` допускают `1d+-`. Ключ `chat|HH:MM|1d+-` уникален и стабилен — противоречия нет; уточнить в плане порядок суффиксов не требуется.

### 2.7. УЛУЧШЕНИЕ. Python 3.11 vs локальная проверка 3.14.

Место: `.github/workflows/summarize.yml:29` (`python-version: '3.11'`). Все прогоны аудита сделаны на python 3.14.4 (единственная версия в venv, `pip -r requirements.txt` встал успешно; `py_compile` OK). Риск расхождения минимален (код не использует 3.12+ синтаксис), но формально приёмка на 3.11 не выполнена. Не подтверждено как баг — вопрос/замечание.

### 2.8. ВОПРОС/УЛУЧШЕНИЕ. `--chat-id` отрицательные значения и argparse.

Место: `run_once.py:40` (`type=int`). CLI-пример плана `--chat-id -100...`: argparse трактует `-100…` как параметр только если он выглядит как отрицательное число (после Python 3.12 с `allow_abbrev` поведение стабильно для int-типов). Проверено локально: `parse_args(['--chat-id','-1001369370434','--period','1d'])` → `chat_id=-1001369370434`. OK, но в workflow ручной прогон идёт через env `CHAT_ID` (summarize.yml:62–63) — корректно.

### 2.9. ФАКТ/ПОДТВЕРЖДЕНИЕ ключевых утверждений плана (проверено, без замечаний):

- Импорт-безопасность VPS-маршрута: без TELEGRAM_SESSION валидация на импорте сохранена (fail-fast как раньше) — симуляция с плейсхолдерами из репозитория даёт `exit(1)`+инструкцию, дифф main.py:156–221 это реализует; приActions-маршруте (непустой TELEGRAM_SESSION) импорт не валидирует (main.py:220–221), и run_once.py дергает `load_env_config()` перед задачами (run_once.py:311–315). Утверждение handoff «поведение VPS не изменилось» — подтверждено структурно (тот же текст ошибок/exit 1; `exit(1)`→`SystemExit(1)` эквивалентны на уровне модуля).
- `override=False` — main.py:163, run_once.py:26; секреты Actions не затираются.
- К2-возвраты: `return False/False/False/True/False` и `return await run_analysis(...)` присутствуют (main.py:2894,2954,2969,3384,3397,3651,3663); VPS-вызовы 3543 и 3680(add_job) возвратами не пользуются — подтверждено grep (в старом файле это были строки 3508/3628 из плана).
- Дедуп-окно и полночь: `test_run_once.py` 8/8 PASS (вывод в окружении с заглушенными ключами, `ALL TESTS PASSED`), включая date_key='2026-01-01' для слота 23:58 при now=00:03.
- Workflow-скелет синтаксически валиден (yaml.safe_load OK; `on:` → `workflow_dispatch.inputs` корректно вложен).
- `persist with if: ${{ !cancelled() }}` — добавлено сверх плана (план: push один раз в конце; реализация — даже при провале шага задач, что лучше соответствует §9 Фазы 3).
- `.gitignore` содержит `state.json` (строка 214+), `*.session`, `private.txt` — как в плане (209–211).
- README добавлен раздел «Запуск на GitHub Actions» + предупреждение о единственной сессии (diff +78 строк) — Фаза 6 выполнена.
- requirements: `apscheduler>=3.10.0` (К1) — Файлы/Порядок п.1 выполнен.
- Утверждение 5.3 проверено: `gh repo view Hohlas/ChatSum --json visibility,isPrivate` → `{"isPrivate":false,"visibility":"PUBLIC"}`; `gh auth status` → protocol ssh, scopes без `workflow`; remote `git@github.com:Hohlas/ChatSum.git` — всё совпадает с планом.
- Утверждение «не передавать inputs через интерполяцию в shell» выполнено (env CHAT_ID/PERIOD, кавычки в строках).
- `gen_session.py` соответствует 2.2: `TelegramClient(StringSession(), …).start(phone=PHONE)` + печать `session.save()`; сессия в repo не коммитится (проверено `git ls-files`, пустой приватный файл не в индексе).
- Ручной режим: `_parse_suffixes` + валидация `\d+[hd]`, exit 2 при неверном периоде (run_once.py:252–258) — соответствует 3.1.
- Коды возврата: 0 — успех/нет задач/все дедуплены; 1 — падение connect или ≥1 упавшей задачи; 2 — preflight/config — соответствует §11 Фазы 3 и handoff-списку.

### 2.10. УЛУЧШЕНИЕ/ВОПРОС. `workflow_dispatch` с заполненным `chat_id` пишет результат, но **не трогает state** (run_manual не читает state) — соответствует назначению «ручной прогон» (3.1), но plan §Порядок не оговаривает; замечания нет.

### 2.11. Улучшение (гигиена). `GOOGLE_API_KEY2/3` переданы в env workflow (summarize.yml:49–50), хотя таблица 5.1 требует `GOOGLE_API_KEY1..N` опционально; при отсутствии секретов → пустые env, `validate_config` фильтрует пустые (`value.strip()` в main.py:72) — безопасно. Уточнить в плане список секретов не нужно.

---

## 3. Команды верификации (воспроизводимость)

1. `python -m venv /tmp/opencode/cs-venv && /tmp/opencode/cs-venv/bin/pip install -r requirements.txt` — успех (APScheduler 3.11.3).
2. `PYTHONPATH=. TELEGRAM_API_ID=12345 TELEGRAM_API_HASH=x TELEGRAM_PHONE=+1 GOOGLE_API_KEY=x ./cs-venv/bin/python test_run_once.py` → ALL TESTS PASSED.
3. `TELEGRAM_SESSION=x TELEGRAM_API_ID=1 TELEGRAM_API_HASH=h ./cs-venv/bin/python -c "import main"` → `ValueError: Not a valid string` (замечание 2.2).
4. `TELEGRAM_SESSION=garbage … run_once.py --list` → понятный exit 2 (защита run_once подтверждена).
5. `TELEGRAM_SESSION= … run_once.py --due` (isolated dir) → создан private.txt из шаблона, затем exit 2 от preflight (замечание 2.3).
6. Симуляция single-branch shallow clone + `git fetch origin state` → `origin/state` НЕ появляется; `git checkout -B state origin/state` fatal; push первой orphan-ветки OK (замечание 2.1). Симуляция wildcard-refspec (`+refs/heads/*:refs/remotes/origin/*`) → `origin/state` создаётся, цикл Fetch→Persist→RUN2 работает (рецепт исправления валидирован).
7. `python -m py_compile main.py run_once.py gen_session.py test_run_once.py` — OK.
8. YAML-parsing summarize.yml — OK; `gh repo view`/`gh auth status` — совпадают с §5.3.

---

## 4. Резюме вердиктов

- Реализация в целом faithful к плану, но **не готова к первому же второму cron-запуску**: workflow-дедуп неработоспособен из-за refspec-ловушки checkout@v4 (2.1) — критично.
- Формальная приёмка плана невыполнима из-за некорректного значения `TELEGRAM_SESSION=x` (2.2) — критично для checklist (исправить тест, не код).
- Семантика bool-возврата шире, чем заявлено в плане, что даёт шум/повторы для пустых периодов (2.4) — важно; требует либо правки контракта, либо дедуп-записи по «empty».
- Пустой секрет TELEGRAM_SESSION = VPS-ветка (2.3) — важно (мусор-файл, риск интерактива при обходе preflight).
- Остальное — улучшения/вопросы; VPS-поведение, тесты чистой логики, README, requirements, ключи дедупа, exit-коды, gitignore — подтверждены.
