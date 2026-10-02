# Аудит: реализация плана plans/GitHubActions.md (вариант B)

Объект проверки: handoff-описание («Изменённые/Новые файлы») + артефакты `main.py`, `run_once.py`, `gen_session.py`, `.github/workflows/summarize.yml`, `test_run_once.py`, `requirements.txt`, `README.md`, `.gitignore` против плана `plans/GitHubActions.md`.

Окружение проверки: локальная машина (Linux), git 2.53; venv `/tmp/opencode/cs-venv` (python 3.14.4) и standalone `/tmp/opencode/py311` (python 3.11.9) с Telethon 1.34.0, APScheduler 3.11.3, httpx 0.28.1, openai 3.23.0, telegraph 2.2.0, python-dotenv 1.0.0. Git-сценарии воспроизведены на репозиториях в `/tmp/opencode/verify`.

> **Ревизия 2026-10-02.** Первая редакция этого аудита содержала ошибки: замечание 2.1 (refspec в workflow) снято — исправление уже было в том же коммите `36fe5fe`; замечание 2.4 (ложные `return False` для пустых периодов) снято — в HEAD там `return True`; замечание 2.7 (Python 3.11) закрыто прямым прогоном. Замечание 2.3 подтверждено и **исправлено в коде** `main.py`. Актуальная картина — ниже.

---

## 1. Итог по критериям

**Локальная корректность.** Реализация соответствует плану по структуре: `load_env_config`/`_config_errors_or_exit`, `_is_actions_mode` отслеживает Actions-профиль, `build_session` со StringSession, возвраты `bool` из `run_analysis`/`scheduled_analysis_job`, `run_once.py` с `--due/--list/--chat-id`, тесты окна/дедупа/полночи/prune, workflow с cron `*/5` + workflow_dispatch + concurrency + `$RUNNER_TEMP` + `read-tree --empty` + явным refspec + rebase-ретраем. `apscheduler>=3.10.0` добавлен в `requirements.txt`. Модульные тесты 8/8 проходят на 3.11 и 3.14.

**Открытые замечания:** 2.2 (некорректная приёмочная команда с `TELEGRAM_SESSION=x`) — дефект формулировки плана, не кода; 2.5, 2.6, 2.8, 2.10, 2.11 — улучшения. Остальное (2.1, 2.3, 2.4, 2.7) снято/исправлено/закрыто.

**Целостность.** Ядро варианта B (дедуп по `chat_id|HH:MM|period` с МСК-датой, запись state после каждого задания, код возврата) реализовано согласованно; `compute_due` даёт ровно одно срабатывание при пересечении полуночи (тест подтверждает), `task_key` совпадает с форматом 4.1 (с оговоркой — см. 2.6).

**Обоснованность подхода.** Выбор ветки `state` + `$RUNNER_TEMP`, `override=False` для dotenv, импорт-тайм валидация только для VPS — аргументированы в плане и корректны сами по себе. Риск «одна сессия одновременно» отражён в README и плане.

---

## 2. Замечания

### 2.1. СНЯТО (поправка 2026-10-02). Ранее: `git fetch origin state` не создаёт `origin/state` при default `fetch-depth` checkout@v4.

**Поправка после перепроверки.** Описанный дефект к HEAD **не относится**: и `.github/workflows/summarize.yml:38,80`, и `plans/GitHubActions.md:332,369` уже используют явный refspec `+refs/heads/state:refs/remotes/origin/state` (исправление внесено в том же коммите `36fe5fe`, что и реализация). Замер на реплике single-branch shallow клона (`remote.origin.fetch='+refs/heads/main:refs/remotes/origin/main'`):
- `git fetch origin state` → exit 0, создаёт только `FETCH_HEAD`, `origin/state` отсутствует (`fatal: invalid object name 'origin/state'`) — это поведение **до** фикса;
- `git fetch --depth=1 origin +refs/heads/state:refs/remotes/origin/state` → `origin/state` создаётся; цикл Fetch→Persist (orphan на первом прогоне, `checkout -B state origin/state` далее)→RUN2 проходит.

Также **неверно прежнее утверждение о существовании ветки**: `git ls-remote origin state` и `git ls-remote origin refs/heads/state` дают пустой вывод (код 0 — «команда выполнена», а не «ссылка найдена»); `git ls-remote origin 'refs/heads/*'` возвращает только `main` и `server-backup`. Ветки `state` **нет**; первый прогон штатно идёт через `--orphan state; git read-tree --empty`.

Итог: замечание снято, дедуп/Persist в текущем виде работоспособны. Источник прежней ошибки: замер по до-фиксовому тексту и неверная трактовка `git ls-remote` (пустой вывод ≠ отсутствие ссылки при exit=0).

### 2.2. СНЯТО (поправка 2026-10-02). Ранее: приёмка `TELEGRAM_SESSION=x python -c "import main"` падает с ValueError.

**Поправка.** Сам дефект верен (строка-пустышка `x` не валидна для Telethon: `StringSession('x')` → `ValueError: Not a valid string`), но к актуальному плану **не относится**: в `plans/GitHubActions.md:572–576` приёмочная команда уже заменена на «валидный StringSession из `gen_session.py`», с явной пометкой, что `TELEGRAM_SESSION=x` невалиден, а проверка безопасного импорта делается через `run_once.py --list` / `preflight_import_env`. Дополнительно после исправления 2.3 пустой/битый секрет отличается от «не задан» и даёт понятный exit 2. Замечание снято.

### 2.3. ИСПРАВЛЕНО (2026-10-02). Пустой секрет TELEGRAM_SESSION уводил раннер на VPS-маршрут.

**Статус.** Был реальный дефект: условие `if not os.getenv('TELEGRAM_SESSION')` не отличало «переменная не задана» (VPS) от «задана пустой» (незаполненный секрет Actions), из-за чего на раннере создавался `private.txt` из шаблона и запускалась импорт-тайм валидация VPS.

**Исправление (внесено):** в `main.py` добавлена `_is_actions_mode()` (проверка `'TELEGRAM_SESSION' in os.environ`) и применена в трёх местах — создание `private.txt`, импорт-тайм `_config_errors_or_exit()` и `build_session()`. `preflight_import_env` в `run_once.py` уже различает пустой/битый секрет (exit 2).

**Проверка на Python 3.11 и 3.14:** `TELEGRAM_SESSION= ... import main` → импорт проходит, маршрут `StringSession`, `private.txt` **не создаётся**; `env -u TELEGRAM_SESSION` → VPS-маршрут (файловая сессия, `private.txt` из шаблона как раньше). `run_once.py --list` при пустом/битом секрете → `❌ ...` и exit 2 до `start()`.

### 2.4. СНЯТО (поправка 2026-10-02). Ранее: «`run_analysis` возвращает False для штатных пустых периодов → бесконечные повторы».

**Поправка после перепроверки.** К HEAD **не относится**: проверка фактических строк даёт
- «нет сообщений за период» → `main.py:2912` — **`return True`**;
- «все сообщения отфильтрованы» → `main.py:2973` — **`return True`**;
- ошибка Gemini (`summary.startswith('❌')`) → `main.py:2988` — `return False`.

То есть пустые/отфильтрованные слоты уже помечаются как «отработанные» (дедуп срабатывает, повторов нет), а `False` остался только для нештатного отказа. Ссылки аудита на `main.py:2894,2954,2969` сдвинуты и указывали на `True`-ветки. Кроме того, сам план (4.4) уже переписан под это поведение («True — слот отработан, включая штатные не-сбойные ветки; False — нужно повторить»). Замечание снято.

### 2.5. УЛУЧШЕНИЕ. `prune_state` несовместим с МСК-датами високосного/конца месяца? Нет; но сравнение строк ISO OK. Реальная проблема: prune удаляет «старые» дедуп-записи ровно на пороге 3 суток — безвредно, но тест `prune` проверяет only 4-дневную метку. Место: `run_once.py:79–85`, `test_run_once.py:87–93`. Существенного багa нет — фиксирую как мелкое улучшение покрытия.

### 2.6. УЛУЧШЕНИЕ. Формат ключа в плане vs реализация.

Место: `GitHubActions.md:240` (`period{suffixes}`) vs `run_once.py:88–95` (суффиксы `+`/`-` конкатенируются в порядке `+` затем `-`). `SCHEDULE.txt` и `load_schedule._parse_suffixes` допускают `1d+-`. Ключ `chat|HH:MM|1d+-` уникален и стабилен — противоречия нет; уточнить в плане порядок суффиксов не требуется.

### 2.7. ЗАКРЫТО (поправка 2026-10-02). Проверка на Python 3.11.

Скачан standalone CPython 3.11.9, создан venv, `pip install -r requirements.txt` (Telethon 1.34.0, APScheduler 3.11.3, openai 3.23.0, httpx 0.28.1, telegraph 2.2.0, python-dotenv 1.0.0). На 3.11: `py_compile` всех файлов — OK; `test_run_once.py` — ALL TESTS PASSED (8/8); `run_once.py --list` при пустом/битом секрете — чистый exit 2 без создания `private.txt`. Расхождение версий закрыто.

### 2.8. ВОПРОС/УЛУЧШЕНИЕ. `--chat-id` отрицательные значения и argparse.

Место: `run_once.py:40` (`type=int`). CLI-пример плана `--chat-id -100...`: argparse трактует `-100…` как параметр только если он выглядит как отрицательное число (после Python 3.12 с `allow_abbrev` поведение стабильно для int-типов). Проверено локально: `parse_args(['--chat-id','-1001369370434','--period','1d'])` → `chat_id=-1001369370434`. OK, но в workflow ручной прогон идёт через env `CHAT_ID` (summarize.yml:62–63) — корректно.

### 2.9. ФАКТ/ПОДТВЕРЖДЕНИЕ ключевых утверждений плана (проверено, без замечаний):

- Импорт-безопасность VPS-маршрута: без `TELEGRAM_SESSION` в окружении валидация на импорте сохранена (fail-fast как раньше) — симуляция с плейсхолдерами даёт exit 1 + инструкцию; в Actions-профиле (`TELEGRAM_SESSION` присутствует, в т.ч. пустой) импорт не валидирует, `run_once.py` дергает `load_env_config()` перед задачами. Различение «не задан»/«задан пустым» — `_is_actions_mode()` (main.py:156), исправление 2.3.
- `override=False` — main.py:163, run_once.py:26; секреты Actions не затираются.
- К2-возвраты (актуальные строки HEAD): «нет сообщений» → `True` (main.py:2912), «всё отфильтровано» → `True` (main.py:2973), ошибка Gemini → `False` (main.py:2988), успех → `True` (main.py:3403), except → `False` (main.py:3416), `scheduled_analysis_job` → `False` (main.py:3670) / `return await run_analysis(...)` (main.py:3666). VPS-вызовы значения не используют.
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

Ревзия 2026-10-02 (оба интерпретатора: 3.14.4 `/tmp/opencode/cs-venv`, 3.11.9 `/tmp/opencode/py311/venv311`):

1. `pip install -r requirements.txt` — успех на 3.11 и 3.14 (APScheduler 3.11.3).
2. `test_run_once.py` (`PYTHONPATH=. TELEGRAM_API_ID=12345 ...`) → `ALL TESTS PASSED` (8/8) на 3.11 и 3.14.
3. `TELEGRAM_SESSION=x ... -c "import main"` → `ValueError: Not a valid string`; приёмочная формулировка плана уже исправлена (2.2).
4. `TELEGRAM_SESSION=garbage ... run_once.py --list` → понятный exit 2 (защита run_once подтверждена).
5. `TELEGRAM_SESSION= ... import main` (Actions-профиль) → маршрут StringSession, `private.txt` **не** создаётся; `env -u TELEGRAM_SESSION ... import main` → VPS-маршрут, `private.txt` из шаблона. Исправление 2.3 подтверждено.
6. Реплика single-branch shallow клона: без явного refspec `git fetch origin state` создаёт только `FETCH_HEAD` (`origin/state` отсутствует); с `+refs/heads/state:refs/remotes/origin/state` `origin/state` создаётся и цикл Fetch→Persist→RUN2 проходит (2.1 снято).
7. `git ls-remote origin state` / `refs/heads/state` → пусто; `'refs/heads/*'` → `main`, `server-backup` (ветки `state` нет; первый прогон создаёт её через `--orphan`).
8. `py_compile` всех файлов — OK на 3.11 и 3.14; YAML summarize.yml парсится (`name/on/permissions/concurrency/jobs`).

---

## 4. Резюме вердиктов

- К HEAD применимы только улучшения 2.5, 2.6, 2.8, 2.10, 2.11 (не блокирующие).
- 2.1 (refspec Fetch/Persist) — **снято**: явный refspec уже в коммите `36fe5fe`.
- 2.2 (приёмка `TELEGRAM_SESSION=x`) — **снято**: план уже требует валидный StringSession / `run_once.py --list`.
- 2.3 (пустой секрет → VPS-маршрут) — **исправлено в коде** (`_is_actions_mode`); проверено на 3.11/3.14.
- 2.4 (ложные `return False` для пустых периодов) — **снято**: в HEAD там `return True` (дедуп работает).
- 2.7 (Python 3.11) — **закрыто** прямым прогоном на 3.11.9.
- VPS-поведение, тесты чистой логики, README, requirements, ключи дедупа, exit-коды, gitignore — подтверждены. Реализация работоспособна; остаётся незакрытым только end-to-end прогон на раннере GitHub (реальная сессия/Gemini/публикация).