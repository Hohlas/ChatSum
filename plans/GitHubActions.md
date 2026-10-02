# План: ChatSum на GitHub Actions (вариант B — опрос + дедупликация)

## Цель

Запускать ежедневные саммари из `SCHEDULE.txt` на бесплатных мощностях **GitHub Actions**,
сохранив возможность запускать бота на обычном VPS **из того же кода**.

Используется **вариант B**: workflow запускается по cron каждые 5 минут, раннер
определяет, какое задание из `SCHEDULE.txt` попало в текущее окно, и **не повторяет**
уже выполненное (дедупликация через состояние). Это устойчиво к задержкам и пропускам
штатного cron GitHub Actions.

## Ключевые принципы

- **Один код — две точки входа**. Никаких форков и копий `main.py`.
  - `main.py` — долгоживущий процесс + APScheduler + интерактивные команды → **VPS**.
  - `run_once.py` — «выполнить одну срочную задачу и выйти» → **GitHub Actions**.
- Общее ядро (`run_analysis`, `collect_messages`, сборка HTML/Telegraph, вызовы Gemini)
  не дублируется и правится в одном месте.
- Поведение VPS-версии **не меняется**: если `TELEGRAM_SESSION` не задан — используется
  файловая сессия `session_name.session`, как сейчас.

## Архитектура

```
GitHub Actions (cron */5, workflow_dispatch)
        │
        ▼
 run_once.py --due
        │  1. git читает SCHEDULE.txt, PROMPT.txt, EXCLUDED_USERS.txt,
        │     PRIORITY_USERS.txt из репозитория (MODEL_CONFIG.txt опционален)
        │  2. StringSession из секрета TELEGRAM_SESSION
        │  3. скачивает state.json (ветка state) и определяет «должные» задания
        │  4. для каждого: main.scheduled_analysis_job(chat_id, period, post_to_source, post_as_telegram)
        │  5. сохраняет state.json обратно (только успешные задачи)
        ▼
 Telegram (MTProto) + Gemini + Telegraph/HTML
```

---

## Фаза 1. Import-safety `main.py`

Сейчас модуль на верхнем уровне делает side effects, из-за чего его нельзя импортировать
из `run_once.py`:

- `ensure_private_file()` — строка 156
- `load_dotenv('private.txt')` — строка 159
- `validate_config()` + `print` + `exit(1)` — строки 162–181
- печать «🔑 Проверка ключей Google AI Studio» и `exit(1)` при недопустимых
  символах ключа — строки 668–681
- чтение `EXCLUDED_USERS.txt` / `PRIORITY_USERS.txt` / `PROMPT.txt` /
  `MODEL_CONFIG.txt` — строки 655–658 (безвредное, но I/O на импорте)
- создание `http_client = httpx.AsyncClient(...)` — строка 684 (683 — комментарий)
- `AsyncIOScheduler()` и регистрация обработчиков — строки 3606+
- `if __name__ == '__main__'` уже есть — строка 4409

### 1.1 Обернуть загрузку окружения в функцию

```python
# --- на уровне модуля (импорт), ДО чтения env на строках 184-209 ---
if not os.getenv('TELEGRAM_SESSION'):       # на Actions private.txt не нужен
    file_just_created = ensure_private_file()
load_dotenv('private.txt', override=False)  # env из секретов Actions не затирается

def load_env_config():
    """Валидирует окружение. Вызывается из main() (VPS) и из run_once.py (preflight)."""
    errors = validate_config()
    if errors:
        ...  # текущий вывод 162-181
        raise SystemExit(1)
    _check_google_key_chars()   # бывший блок 668-681 (печать + exit(1))
```

- **Порядок env важен:** `API_ID = int(os.getenv(...))`, `PHONE` и прочие (184–209) читаются
  на **уровне модуля**, поэтому `load_dotenv('private.txt')` обязан отработать **на импорте**,
  до строки 184 — иначе VPS упадёт с `int(None)`. Значит:
  - на уровне модуля оставить `ensure_private_file()` + `load_dotenv('private.txt', override=False)`
    (при `TELEGRAM_SESSION` в env `ensure_private_file()` можно пропустить, чтобы не создавать
    `private.txt` с заглушками на раннере);
  - `validate_config()` + `print` + `exit(1)` (162–181) вынести в `load_env_config()`;
  - `load_env_config()` вызывать в `main()` (VPS, строго) и в preflight `run_once.py`
    (Actions: не валить импорт, а давать понятную ошибку с `exit(1)` до `start()`).
- Перенести в `load_env_config()` блок проверки/печати ключей (строки 668–681), сейчас
  выполняющийся на импорте и содержащий `exit(1)`.
- К `httpx.AsyncClient` (строка 684) — единый клиент, переиспользуется всеми вызовами;
  не создавать второй. Для `main()` закрывается в `finally` (строка 4397) уже сейчас.
  В `run_once.py` он должен закрываться явно либо процесс завершается — **не вызывать
  `aclose()` до конца задач**, иначе клиент будет закрыт раньше вызовов Gemini.
- `CURRENT_MODEL, USE_REASONING, USE_HTML_EXPORT = load_model_config(...)` (строка 658)
  выполняется на импорте и **должен остаться**: значение `CURRENT_MODEL` используется на
  уровне модуля (строка 660). Для `run_once.py` этого достаточно: `main` импортируется
  уже после checkout репозитория, поэтому `MODEL_CONFIG.txt` (если добавлен) прочитается
  на импорте; при отсутствии файла берутся дефолты (см. 5.2).
- **Критерий готовности уточняется:** импорт не падает и не требует интерактива
  (без `exit(1)` и без блокирующего ввода). Побочные эффекты чтения файлов и создания
  клиента допускаются.

### 1.2 Проверить зависимости от значений на этапе импорта

- `CURRENT_MODEL, USE_REASONING, USE_HTML_EXPORT = load_model_config(...)` — строка 658 —
  безопасно, читает существующий/отсутствующий файл.
- `telegram_client = TelegramClient(...)` — строка 663 — создать, но не запускать
  (connect происходит в `main()`). Это ок для импорта.
- Обработчики `@telegram_client.on(...)` — регистрируются при импорте, не запускаются.
  Для Actions-раннера они безвредны (сообщения не приходят — процесс короткий).

**Критерий готовности:** `python -c "import main"` не падает и не требует интерактива
(без `exit(1)` и без блокирующего ввода); фоновые I/O-эффекты (чтение конфигов, создание
`http_client`) допускаются. Зависимость `apscheduler` обязана быть установлена — см. ниже.

### 1.3 Зависимости импорта (К1) — принято: добавить в requirements

`main.py` на верхнем уровне делает `from apscheduler... import AsyncIOScheduler` (16–17)
и тут же `scheduler = AsyncIOScheduler()` (3606), поэтому `import main` требует `apscheduler`.

**Решение (принято):** добавить `apscheduler` в `requirements.txt`. Это 1 строка, VPS уже
фактически использует APScheduler. Альтернатива «изолировать в VPS-ветку» **недостаточна
одним переносом импорта**: инстанцирование `scheduler = AsyncIOScheduler()` тоже стоит на
уровне модуля (3606) и упадёт без пакета; при выборе этой альтернативы пришлось бы заводить
`scheduler = None` глобально и создавать объект внутри `main()` — заметно больший дифф.
Поэтому requirements-вариант основной.

Команда проверки: `pip install -r requirements.txt && python -c "import main"`.

---

## Фаза 2. StringSession для Actions

### 2.1 Выбор типа сессии

Строка 663:

```python
from telethon.sessions import StringSession

def build_session():
    session_str = os.getenv('TELEGRAM_SESSION', '').strip()
    if session_str:
        return StringSession(session_str)      # GitHub Actions
    return 'session_name'                       # VPS, файловая сессия

telegram_client = TelegramClient(build_session(), API_ID, API_HASH)
```

- `TELEGRAM_SESSION` задан → **Actions**, состояние в секрете, ФС не нужна.
- `TELEGRAM_SESSION` не задан → **VPS**, поведение как сейчас, `session_name.session`.
- Импорт `StringSession` добавить в блок импортов.

> Важно: **нельзя** одновременно запускать VPS- и Actions-версию с одной сессией —
> одновременная работа двух процессов с одной сессией не поддерживается; возможны
> рассинхрон состояния апдейтов и разрыв авторизации. Использовать одну площадку за раз.

### 2.2 `gen_session.py` (новый, запускается локально один раз)

- Создаёт `TelegramClient(StringSession(), API_ID, API_HASH)`, `start(phone=PHONE)`.
- Печатает строку `client.session.save()`.
- Пользователь копирует её в GitHub Secret `TELEGRAM_SESSION`.
- В репозиторий файл не коммитит сессию; `.session` уже в `.gitignore` (строки 209–210:
  `*.session`, `*.session-journal`; 211 — `private.txt`).

---

## Фаза 3. `run_once.py` (новый раннер)

### 3.1 Режимы CLI

```
python run_once.py --due                 # основной режим Actions: выполнить всё, что попало в окно
python run_once.py --chat-id -100... --period 1d [--post-source] [--post-tg]  # ручной прогон
python run_once.py --list                # напечатать расписание (диагностика)
```

### 3.2 Алгоритм `--due`

1. Определить текущее время UTC и МСК (`MSK = UTC+3`, как в `main.py:28`).
2. Сформировать окно: `[now_msk - LAG_MAX, now_msk]`, где `LAG_MAX` — допуск опоздания
   (например, 15 мин). Границы — с округлением к минуте.
3. Прочитать `SCHEDULE.txt` через `main.load_schedule` (строка 3546).
4. Для каждой записи вычислить «ближайшее прошедшее наступление HH:MM» (в пределах суток).
   Задание считается должным, если это время попало в окно. Вычисление вести в UTC
   (`naive`-время без привязки к дате), а ключ задания — по МСК-дате, чтобы окно около
   полуночи МСК не путало «предыдущие сутки» и «текущие».
5. Загрузить `state.json` (см. Фаза 4) и отфильтровать уже выполненные по ключу.
6. Проверить обязательные env (`TELEGRAM_SESSION`, `TELEGRAM_API_ID/HASH`); при
   отсутствии — понятная ошибка и `exit(1)` **до** `start()`, без интерактива.
7. `await telegram_client.start(phone=PHONE)`.
8. Для каждого должного задания вызвать
   `main.scheduled_analysis_job(chat_id, period, post_to_source, post_as_telegram)`
   (строка 3609) — он сам выполняет `get_entity` → `title` и делегирует в `run_analysis`
   (сигнатура, строка 2821). Не дублировать резолв имени.
9. **После каждого задания** (не в конце!) записывать в state **только реально успешные**:
   если раннер/сессия умрёт посреди прогона, уже выполненные задачи не повторятся, а
   упавшая — останется не-записанной и будет повторена в следующий прогон. Запись в файл
   `state.json` — после каждого задания; push в ветку `state` — один раз в конце шага
   Persist (иначе лишние пуши на каждое задание).
10. `await telegram_client.disconnect()`.
11. Код возврата: 0 — все должные задачи успешны; не 0 — если хотя бы одна упала
    (чтобы видеть красный run и позволить повтор).

> **Критично для status (К2):** `run_analysis` и `scheduled_analysis_job` глотают
> исключения (`main.py:3352-3362`, `main.py:3614-3616`) и возвращают `None` даже при
> полном провале. Одного вызова недостаточно для шага 9/11.
>
> **Решение (принято):** минимальный рефакторинг — `run_analysis` возвращает `bool`;
> `scheduled_analysis_job` возвращает результат наружу; VPS-вызовы (строки 3508, 3628)
> продолжают работать как раньше (не используют return, ошибки уже логируются внутри).
> Альтернатива «обёртка в `run_once.py` ловит ошибки / флаг до-после» **неработоспособна**:
> исключение внутри `run_analysis` уже перехвачено (`main.py:3352`), наружу ничего не
> выходит, ловить нечего. Подробный контракт — в Фазе 4.4 и разделе handoff.

### 3.3 Разрешение `chat_name`

Как в `scheduled_analysis_job` (строка 3609): `get_entity(chat_id)` → `.title`.
В `run_once.py` вызвать `main.scheduled_analysis_job(chat_id, period, post_to_source, post_as_telegram)`
напрямую — он уже делает всё нужное. Это минимизирует дублирование.

### 3.4 Что НЕ делать в Actions

- Не стартовать `scheduler` (APScheduler) — расписание отрабатывает `run_once.py`.
- Не ждать `run_until_disconnected()` — после задач сразу disconnect и exit.
- Не обрабатывать интерактивные команды (`/sch`, `/set_model` и т.п.) — они недоступны.

---

## Фаза 4. Дедупликация и состояние (суть варианта B)

### 4.1 Формат `state.json`

```json
{
  "last_run_utc": "2026-10-01T03:05:12Z",
  "completed": {
    "-1001369370434|06:55|1d+": "2026-10-01",
    "-1001892263845|06:50|1d+": "2026-10-01"
  }
}
```

- Ключ: `f"{chat_id}|{HH:MM}|{period}{suffixes}"`.
- Значение: дата (МСК) последнего выполнения. Повтор в ту же дату не запускается.
- Хранить `completed` только за последние ~3 суток (чистка при записи).

### 4.2 Где хранить состояние

Приоритет — **отдельная ветка `state`** (переживает всё, не зависит от eviction кэша):

- Ветка `state` содержит только `state.json`.
- Раннер читает его через `git fetch origin +refs/heads/state:refs/remotes/origin/state` +
  `git show origin/state:state.json` (или `actions/checkout` ветки state в подкаталог).
  Явный refspec обязателен: checkout@v4 по умолчанию делает shallow single-branch fetch без
  wildcard-refspec, поэтому `git fetch origin state` (без refspec) НЕ создаёт `origin/state`.
- После прогона — коммит и `git push origin state`.
- Требует `permissions: contents: write` в workflow.

Альтернатива (проще, но ненадёжнее): `actions/cache` с ключом `state-<run_id>` и
`restore-keys: state-`. Кэш может быть вытеснен; для гарантии — fallback на ветку.

**Решение по плану:** ветка `state`, с опциональным кэшем как ускоритель.

### 4.3 Защита от гонок

- `concurrency: { group: chatsum, cancel-in-progress: false }` в workflow —
  два запуска не пересекутся.
- При push в `state` обрабатывать non-fast-forward (retry: `git pull --rebase`).

### 4.4 Контракт статуса успеха (К2) — принято

Реализуется минимальным рефакторингом **без изменения поведения VPS**:

1. `run_analysis` (`main.py:2821`): оставить внешний `try/except Exception` (3352-3362,
   он шлёт сообщение об ошибке в Telegram), но
   - в `except` добавить `return False`;
   - в самом конце функции (после успешного ветвления `/sum` и `/copy`) добавить `return True`.
   - **Проверить все ранние `return` внутри `run_analysis`**: сейчас их нет, но если
     появятся — каждый должен возвращать `bool`, а не `None`.
2. `scheduled_analysis_job` (`main.py:3609`):
   - `get_entity` fail (3614-3616) → `return False` (было `return`);
   - в конце → `return await run_analysis(...)`.
3. VPS-вызовы (3508, 3628) **не трогаем** — их и так оборачивает логика/игнор возврата.
4. `run_once.py` по каждому заданию: `ok = await main.scheduled_analysis_job(...)`;
   писать в state только при `ok is True`; накапливать флаг ошибки для кода возврата.

Сигнал достоверен: `True` — «слот отработан»: нормальное завершение публикации, а также
штатные не-сбойные ветки, когда задание выполнено сообщением в Telegram и повторять его
нет смысла (нет сообщений за период; все сообщения отфильтрованы). `False` — «нужно
повторить»: любое перехваченное исключение или нештатный отказ (например, ошибка Gemini
`❌`-summary). Вариант «обёртка в run_once.py» отклонён: исключения внутри `run_analysis`
не пробрасываются, их нечем ловить.

---

## Фаза 5. GitHub Actions workflow

Файл `.github/workflows/summarize.yml`:

```yaml
name: ChatSum scheduled summaries
on:
  schedule:
    - cron: '*/5 * * * *'   # UTC; раннер сам решает, что «должно» в МСК
  workflow_dispatch:          # ручной запуск из UI
    inputs:
      chat_id:
        description: 'Только для ручного прогона конкретного чата'
        required: false
      period:
        description: 'Период (1d, 12h)'
        required: false

permissions:
  contents: write

concurrency:
  group: chatsum
  cancel-in-progress: false

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt

      # принести state.json из ветки state ВО ВНЕ рабочего дерева ($RUNNER_TEMP),
      # чтобы untracked-файл не конфликтовал с git checkout при Persist
      - name: Fetch state
        run: |
          git fetch --depth=1 origin +refs/heads/state:refs/remotes/origin/state 2>/dev/null || true
          git show origin/state:state.json > "$RUNNER_TEMP/state.json" 2>/dev/null \
            || echo '{}' > "$RUNNER_TEMP/state.json"

      - name: Run due summaries
        env:
          TELEGRAM_API_ID:   ${{ secrets.TELEGRAM_API_ID }}
          TELEGRAM_API_HASH: ${{ secrets.TELEGRAM_API_HASH }}
          TELEGRAM_PHONE:    ${{ secrets.TELEGRAM_PHONE }}
          TELEGRAM_SESSION:  ${{ secrets.TELEGRAM_SESSION }}
          TELEGRAM_GROUP_ID: ${{ secrets.TELEGRAM_GROUP_ID }}
          GOOGLE_API_KEY:    ${{ secrets.GOOGLE_API_KEY }}
          GOOGLE_API_KEY1:   ${{ secrets.GOOGLE_API_KEY1 }}
          GEMINI_MODEL:      ${{ vars.GEMINI_MODEL }}
          GEMINI_TEMPERATURE:      ${{ vars.GEMINI_TEMPERATURE }}
          GEMINI_REASONING_EFFORT: ${{ vars.GEMINI_REASONING_EFFORT }}
          GEMINI_CHUNK_MAX_CHARS:  ${{ vars.GEMINI_CHUNK_MAX_CHARS }}
          # inputs передаём через env, НЕ через интерполяцию ${{ }} в shell
          # (защита от quoting/инъекций; пустой period → дефолт 1d)
          CHAT_ID: ${{ github.event.inputs.chat_id }}
          PERIOD:  ${{ github.event.inputs.period }}
          # run_once.py читает/пишет state по этому пути (вне рабочего дерева)
          STATE_PATH: ${{ runner.temp }}/state.json
        run: |
          if [ -n "$CHAT_ID" ]; then
            python run_once.py --chat-id "$CHAT_ID" --period "${PERIOD:-1d}"
          else
            python run_once.py --due
          fi

      - name: Persist state
        run: |
          # state.json лежит в $RUNNER_TEMP (вне дерева) — см. Fetch state.
          # ВАЖНО: при первом запуске git checkout --orphan НЕ очищает дерево,
          # поэтому используем read-tree --empty, иначе весь код попадёт в ветку state.
          git config user.name  "chatsum-bot"
          git config user.email "chatsum-bot@users.noreply.github.com"
          if git fetch --quiet --depth=1 origin +refs/heads/state:refs/remotes/origin/state 2>/dev/null; then
            git checkout -B state origin/state
          else
            git checkout --orphan state
            git read-tree --empty
          fi
          # ветка state содержит только state.json
          cp "$RUNNER_TEMP/state.json" state.json
          git add -f state.json
          git commit -m "state: $(date -u +%Y-%m-%dT%H:%M:%SZ)" || true
          git push origin state
```

> **Почему state.json вне дерева:** если писать его в корень репозитория, то
> `git checkout -B state origin/state` упадёт — untracked `state.json` конфликтует с
> отслеживаемым файлом целевой ветки («untracked working tree files would be overwritten»).
> Хранение в `$RUNNER_TEMP` и `cp` уже **после** переключения ветки это устраняет.
>
> Альтернатива без `cp` — отдельный checkout ветки состояния
> (`actions/checkout@v4 with: { ref: state, path: state-dir }`) и чтение/запись state по
> `state-dir/state.json`. Оба варианта рабочие; выбран `$RUNNER_TEMP`, т.к. не требует
> второго checkout и не смешивает деревья.
>
> Push выполняется `GITHUB_TOKEN`; `permissions: contents: write` задан на уровне workflow
> выше. Для `schedule`-триггера push ботом не порождает новый run. При гонках
> (`concurrency` уже сериализует запуски) возможен non-fast-forward — обрабатывать
> `git pull --rebase` + повторный `push` (см. 4.3).

### 5.1 Секреты и переменные

Секреты (Settings → Secrets and variables → Actions → Secrets):

| Секрет | Обязателен | Назначение |
|---|---|---|
| `TELEGRAM_API_ID` | да | Telegram API |
| `TELEGRAM_API_HASH` | да | Telegram API |
| `TELEGRAM_PHONE` | да | вход Telethon |
| `TELEGRAM_SESSION` | да | StringSession (Actions) |
| `TELEGRAM_GROUP_ID` | нет | канал результатов |
| `GOOGLE_API_KEY` | да | Gemini |
| `GOOGLE_API_KEY1..N` | нет | ротация ключей |

Переменные (Variables): `GEMINI_MODEL`, `GEMINI_TEMPERATURE`, `GEMINI_REASONING_EFFORT`,
`GEMINI_CHUNK_MAX_CHARS` — не секретные. Передавать все, иначе при «одном коде» поведение
Gemini на Actions разойдётся с VPS (переменные читаются в `main.py:202-209`).

Дефолты **зависят от модели**, поэтому «60000 для всех» — неверно: `get_model_generation_config`
(`main.py:240-293`) задаёт `chunk_max_chars=60000` только для `gemini-2.5-flash` (265), а для
`gemini-3.6-flash` / `gemini-3.5-flash-lite` — `100000` (254-260); базовый fallback —
`DEFAULT_CHUNK_MAX_CHARS=60000` (227). Если `GEMINI_CHUNK_MAX_CHARS` не задан, действует
модельный дефолт.

`GEMINI_TEMPERATURE` пусто/не задан → `0` (204-209). **Важно про `GEMINI_REASONING_EFFORT`:**
при пустом значении `get_model_generation_config` печатает warning «Неверное значение...» на
**каждый вызов** генерации (273-277), потому что пустая строка не равна `none` и не входит в
`ALLOWED_REASONING_EFFORTS`. Чтобы избежать спама в логах Actions, обязательно задать
`GEMINI_REASONING_EFFORT` (например, `none`), а не оставлять пустым.

### 5.2 Конфиги в репозитории

Коммитятся и правятся через git: `SCHEDULE.txt`, `PROMPT.txt`, `EXCLUDED_USERS.txt`,
`PRIORITY_USERS.txt`. `MODEL_CONFIG.txt` в репозитории **отсутствует** и опционален:
при отсутствии `USE_HTML_EXPORT` дефолтится в `true` (HTML-экспорт), а `MODEL` берётся из
`GEMINI_MODEL` (`main.py:580-588`, `616-617`). Если нужно управлять `USE_HTML_EXPORT`
через git — файл надо добавить в репозиторий (см. «Файлы»). Интерактивные команды в
Actions недоступны — расписание меняется редактированием `SCHEDULE.txt` и push.

### 5.3 Настройка репозитория и доступа (проверено)

- **Visibility:** `Hohlas/ChatSum` — **PUBLIC** (`gh repo view Hohlas/ChatSum --json visibility,isPrivate`
  → `{"isPrivate": false, "visibility": "PUBLIC"}`). Значит лимит 2000 мин/мес неактуален:
  standard hosted-раннеры для public-репозиториев бесплатны без ограничения минут (GitHub billing docs).
  Публичными становятся код и `SCHEDULE.txt`; секреты Actions при этом не раскрываются (см. ниже).
- **Секреты в public-репо:** repository/environment secrets всегда зашифрованы и не видны публично,
  не читаются обратно, маскируются в логах; не передаются в workflow из форков; `workflow_dispatch`
  может запустить только пользователь с write-доступом. `vars` (Variables) — **публичны**; если
  `GEMINI_*` надо скрыть, переносить их из `vars` в `secrets`. «Секрет по ссылке с паролем» у
  GitHub отсутствует; large-secret (>48 КБ) делается через `gpg`-блоб в репо + passphrase в секрете.
- **Ветка `state` в public-репо:** публична; содержит только `chat_id`/МСК-даты (не креды) — приемлемо.
- **Токен/доступ для пуша:** `gh` авторизован как `Hohlas`, scopes `repo, read:org, gist,
  admin:public_key` — **без `workflow`**. Scope `workflow` нужен **только** для добавления/изменения
  файлов `.github/workflows/*` при push **через HTTPS с gh-токеном** (REST `contents` API или
  `gh auth setup-git`). При push по **SSH** scope не требуется (`workflow` не действует на SSH).
- **Текущий remote:** `origin` переключён на SSH — `git@github.com:Hohlas/ChatSum.git`
  (пользователь выполнил `git remote set-url origin ...`). `~/.ssh/config` уже маршрутизирует
  GitHub через `ssh.github.com:443` c `~/.ssh/git_key`; `ssh -T git@github.com` успешен
  («Hi Hohlas! You've successfully authenticated»). Credential helper для HTTPS не настроен —
  он и не нужен при SSH-origin.
- **Вывод:** можно не выполнять `gh auth refresh -s workflow`, если workflow-файл пушится по SSH.
  Обновлять scope стоит лишь при переходе на HTTPS-push/API-коммиты workflow-файлов.

---

## Фаза 6. VPS-совместимость

- `main.py` продолжает работать как прежде: `load_env_config()` вызывается в `main()`,
  `TELEGRAM_SESSION` не задан → файловая сессия.
- `telegram-bot.service.example` и `SCHEDULE.txt` — без изменений.
- README: добавить раздел «Запуск на GitHub Actions» и «Выбор площадки» с предупреждением
  о единственной активной сессии.

---

## Фаза 7. Тестирование

1. **Import-safety:** `python -c "import main"` — без ошибок и без интерактива.
2. **VPS-регресс:** запустить `python main.py`, проверить `/sum`, `/sch_list`, срабатывание
   APScheduler (в тестовое время).
3. **StringSession:** `python gen_session.py`, получить строку, проверить `run_once.py --list`
   с заданным `TELEGRAM_SESSION`.
4. **Дедуп:** на тестовом `SCHEDULE.txt` с временем «сейчас − 3 мин»:
   - первый прогон `--due` выполняет задачу;
   - второй прогон сразу же — **не выполняет** (state).
5. **Окно:** время «сейчас − 20 мин» при `LAG_MAX=15` — не выполняется (защита от повторов
   старых задач); при `LAG_MAX=30` — выполняется один раз.
6. **Ручной режим:** `workflow_dispatch` с `chat_id`/`period`.
7. **Ошибки:** неверный/пустой `TELEGRAM_SESSION` → понятный exit-код и лог до `start()`.
8. **Полночь МСК:** слот около 00:00 МСК (например, `23:58`) и окно `[now−15min, now]`,
   пересекающее полночь — задание должно быть помечено корректно ровно один раз.

---

## Риски и ограничения

- **Cron GitHub Actions** может опаздывать/пропускаться — компенсируется окном + дедупом.
- **Public-репозиторий**: cron отключается после 60 дней без активности — периодически
  коммитить (иначе расписание замолчит).
- **Лимиты минут**: `Hohlas/ChatSum` — public (см. 5.3), standard-раннеры бесплатны без
  лимита минут, поэтому `*/5` допустим. Если бы репо был private: 2000 мин/мес бесплатно,
  прогон 1–3 мин, при `*/5` ~8640 запусков/мес → превышение. Тогда: реже опрашивать
  (`*/15`) и/или запускать только в окнах около времени из `SCHEDULE.txt`; **предпочтительно**
  генерировать cron-шаблон под конкретные слоты (06:00, 06:15, 06:50, 06:55, 07:15 МСК →
  UTC-эквиваленты), а `--due` оставить как страховку. Для public оптимизация не обязательна,
  но полезна.
- **Публичность кода**: код и `SCHEDULE.txt` видны всем; секреты Actions остаются скрытыми
  (см. 5.3). `vars` (Variables) — публичны: если `GEMINI_*` надо скрыть, держать в `secrets`.
  Ветка `state` публична, но содержит только неконфиденциальные `chat_id`/даты.
- **Доступ для push workflow-файлов**: `gh`-токен без scope `workflow`; при SSH-origin
  (текущий) scope не нужен. При HTTPS-push/API-коммитах `.github/workflows/*` — выполнить
  `gh auth refresh -h github.com -s workflow`.
- **Единственная сессия**: не запускать VPS и Actions одновременно.
- **StringSession** требует перевыпуска при ревоке/смене пароля.
- **Секреты** не логировать; `private.txt` в Actions не нужен.

## Порядок работ

Решения по шагу 0 **приняты** (не переобсуждать при реализации):

- **К1:** добавить `apscheduler` в `requirements.txt`.
- **К2:** `run_analysis -> bool` (контракт в 4.4), VPS-вызовы не меняют поведение.
- **Cron:** сразу `*/5 * * * *` + `--due` (public-репо, минуты бесплатны).
- **state:** хранить в `$RUNNER_TEMP`, писать файл после каждого задания, push — один раз.

1. `requirements.txt`: `+apscheduler` (К1).
2. Import-safety `main.py`: Фаза 1 (+ `load_env_config`, вынос печати/`exit(1)`), Фаза 2
   (`build_session`/StringSession), К2 (`run_analysis`/`scheduled_analysis_job` → `bool`).
3. `gen_session.py`.
4. `run_once.py` (CLI, `--due`, окно `LAG_MAX`, дедуп `state.json`, preflight env, exit-коды).
5. `.gitignore`: добавить `state.json` (в `main` его быть не должно).
6. `.github/workflows/summarize.yml` (готовый YAML из Фазы 5).
7. README: раздел «Запуск на GitHub Actions» + предупреждение о единственной сессии.
8. Тесты из Фазы 7 и приёмка из раздела «Handoff».

## Файлы

- изменяются: `main.py` (Фаза 1, 2, К2), `requirements.txt` (+`apscheduler`),
  `.gitignore` (+`state.json`), `README.md`
- новые: `run_once.py`, `gen_session.py`, `.github/workflows/summarize.yml`, ветка `state`
- опционально новый: `MODEL_CONFIG.txt` (если нужно управлять `USE_HTML_EXPORT` через git;
  сейчас файла в репозитории нет — при отсутствии используется HTML-экспорт по умолчанию)
- без изменений: `SCHEDULE.txt`, `PROMPT.txt`, `telegram-bot.service.example`,
  `private.txt.example`

---

## Handoff: задание для агента-исполнителя

Кратко и по делу — что сделать и как проверить. Детали выше по фазам.

### Инварианты (не нарушать)
- **Один код, две точки входа.** `main.py` не форкать; `run_once.py` импортирует общее ядро.
- **VPS-поведение не меняется.** Если `TELEGRAM_SESSION` не задан — файловая сессия
  `session_name.session`, обычный запуск `python main.py`.
- **Общее ядро не дублировать**: сбор сообщений, Gemini, HTML/Telegraph, `run_analysis`.
- Публичный API `run_analysis`/`scheduled_analysis_job` сохраняем (позиционные аргументы,
  имена), добавляем только возврат `bool`.
- Не коммитить креды/сессии; `private.txt` и `*.session` не трогать.

### Порядок (по приоритету блокировки)
1. `requirements.txt` + `apscheduler`.
2. `main.py` Фаза 1: `load_env_config()` для 162-181 и 668-681; вызов в `main()`.
   **Осторожно:** `API_ID=...` (184-209) и `TelegramClient(...)` (663) выполняются на импорте —
   env к ним должен быть доступен. Не сломать загрузку `TELEGRAM_SESSION` на Actions.
3. `main.py` Фаза 2: `build_session()` по образцу 2.1.
4. `main.py` К2: возвраты `bool` (4.4).
5. `run_once.py` (Фаза 3, 4, 4.4): `--due/--list/--chat-id/--period/--post-source/--post-tg`;
   окно `LAG_MAX` (по умолчанию 15 мин), МСК-ключ, чтение/запись `state.json`
   по `STATE_PATH` (env, если задан) иначе локально; preflight env до `start()`.
6. `.gitignore` + `state.json`.
7. `.github/workflows/summarize.yml` — из Фазы 5 как есть (уже учитывает `$RUNNER_TEMP`).
8. `README.md` — раздел про Actions + предупреждение о единственной сессии.

### Обязательные проверки (Фаза 7)
- `pip install -r requirements.txt` и импорт на Actions-профиле с валидным StringSession
  из `gen_session.py`: `TELEGRAM_SESSION=<валидный StringSession> python -c "import main"` — без ошибок.
  (Примечание: строка-пустышка `TELEGRAM_SESSION=x` НЕ валидна для Telethon и уронит импорт
  `StringSession('x')` — проверка безопасного импорта выполняется в `run_once.py --list`,
  см. `preflight_import_env`.)
- `python main.py` на локальном VPS-профиле — env грузится, `/sch_list` работает, APScheduler жив.
- `python run_once.py --list` — печатает расписание.
- Дедуп: два `--due` подряд на слоте «сейчас−3мин» → второй no-op.
- Окно: «сейчас−20мин» при `LAG_MAX=15` не срабатывает; при `LAG_MAX=30` — срабатывает.
- Полночь МСК: слот `23:58`, окно пересекает полночь → ровно одно срабатывание.
- Пустой/битый `TELEGRAM_SESSION` → понятный exit до `start()`, не зависание.
- `run_once.py` возвращает 0 при успехе и не 0 при хотя бы одной упавшей задаче.

### Критерий приёмки
- `import main` безопасен; VPS-регресс зелёный; дедуп/окно/полночь покрыты выводами команд.
- В `main` нет `state.json`; ветка `state` содержит только `state.json`.
- `run_analysis`/`scheduled_analysis_job` возвращают `bool`; VPS-вызовы не изменены.
