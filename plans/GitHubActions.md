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
- создание `http_client = httpx.AsyncClient(...)` — строка 683
- `AsyncIOScheduler()` и регистрация обработчиков — строки 3606+
- `if __name__ == '__main__'` уже есть — строка 4409

### 1.1 Обернуть загрузку окружения в функцию

```python
def load_env_config():
    """Создаёт/читает private.txt и валидирует окружение.
    На VPS вызывается из main(); при импорте из run_once.py — не вызывается."""
    file_just_created = ensure_private_file()
    load_dotenv('private.txt', override=False)   # важно: env из секретов Actions не затирается
    errors = validate_config()
    if errors:
        ...  # текущий вывод
        raise SystemExit(1)
```

- Вызвать `load_env_config()` внутри `async def main()` (строка 4302) в самом начале,
  **до** обращения к `API_ID/API_HASH`.
- Чтение env (`API_ID = int(os.getenv(...))`, строки 184–204) оставить на верхнем уровне:
  к моменту импорта `run_once.py` переменные уже переданы через `env:` workflow.
  Если `TELEGRAM_SESSION` в Actions не нужен `private.txt`, `load_env_config()` не зовём.
- Перенести в `load_env_config()` блок проверки/печати ключей (строки 668–681), сейчас
  выполняющийся на импорте и содержащий `exit(1)`.
- К `httpx.AsyncClient` (строка 683) — единый клиент, переиспользуется всеми вызовами;
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

### 1.3 Зависимости импорта

`main.py` на верхнем уровне делает `from apscheduler.schedulers.asyncio import AsyncIOScheduler`
(строки 16–17), поэтому `import main` из `run_once.py` требует `apscheduler`. Для чистого
раннера он должен ставиться: **либо** добавить `apscheduler` в `requirements.txt`, **либо**
перенести импорт APScheduler в `main()` (VPS-ветка), чтобы общее ядро не тянуло его. Это
меняет `requirements.txt` — см. раздел «Файлы» и команду проверки `python -c "import main"`.

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
- В репозиторий файл не коммитит сессию; `.session` уже в `.gitignore` (строки 209–211).

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
9. Записать в state **только реально успешные** задания (см. про status ниже).
10. `await telegram_client.disconnect()`.
11. Код возврата: 0 — все должные задачи успешны; не 0 — если хотя бы одна упала
    (чтобы видеть красный run и позволить повтор).

> **Критично для status (К2):** `run_analysis` и `scheduled_analysis_job` глотают
> исключения (`main.py:3352-3362`, `main.py:3614-3616`) и возвращают `None` даже при
> полном провале. Поэтому одного вызова недостаточно для шага 9/11. Нужен явный сигнал:
> либо обёртка в `run_once.py`, которая ловит/считает ошибки (при необёрнутом `run_analysis`
> установить флаг до/после и сравнить), либо минимальный рефакторинг — `run_analysis`
> возвращает `bool`/пробрасывает, а VPS-вызовы (строки 3508, 3628) продолжают глотать
> через свой `try`. Требуется синхронная правка record-state: фиксировать успех только
> при подтверждённом результате. Принять решение до реализации Фазы 4.

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
- Раннер читает его через `git fetch origin state` + `git show origin/state:state.json`
  (или `actions/checkout` ветки state в подкаталог).
- После прогона — коммит и `git push origin state`.
- Требует `permissions: contents: write` в workflow.

Альтернатива (проще, но ненадёжнее): `actions/cache` с ключом `state-<run_id>` и
`restore-keys: state-`. Кэш может быть вытеснен; для гарантии — fallback на ветку.

**Решение по плану:** ветка `state`, с опциональным кэшем как ускоритель.

### 4.3 Защита от гонок

- `concurrency: { group: chatsum, cancel-in-progress: false }` в workflow —
  два запуска не пересекутся.
- При push в `state` обрабатывать non-fast-forward (retry: `git pull --rebase`).

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

      # принести state.json из ветки state
      - name: Fetch state
        run: |
          git fetch origin state || true
          git show origin/state:state.json > state.json 2>/dev/null || echo '{}' > state.json

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
        run: |
          if [ -n "${{ github.event.inputs.chat_id }}" ]; then
            python run_once.py --chat-id "${{ github.event.inputs.chat_id }}" \
              --period "${{ github.event.inputs.period }}"
          else
            python run_once.py --due
          fi

      - name: Persist state
        run: |
          # state.json записан run_once.py в корне рабочего дерева.
          # ВАЖНО: при первом запуске git checkout --orphan НЕ очищает дерево,
          # поэтому используем read-tree --empty, иначе весь код попадёт в ветку state.
          git config user.name  "chatsum-bot"
          git config user.email "chatsum-bot@users.noreply.github.com"
          if git fetch origin state 2>/dev/null; then
            git checkout -B state origin/state
          else
            git checkout --orphan state
            git read-tree --empty
          fi
          # ветка state содержит только state.json
          git add -f state.json
          git commit -m "state: $(date -u +%Y-%m-%dT%H:%M:%SZ)" || true
          git push origin state
```

> Точную реализацию push в `state` можно вынести в отдельный шаг либо в сам
> `run_once.py` через `git` subprocess. Push выполняется `GITHUB_TOKEN`;
> для `schedule`-триггера push ботом не порождает новый run.
> (`permissions: contents: write` задан на уровне workflow выше.)
> Рекомендуемый вариант — отдельный checkout ветки state
> (`actions/checkout@v4 with: { ref: state, path: state-dir }`), чтобы не смешивать
> рабочее дерево кода и ветку состояния.

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
Gemini на Actions разойдётся с VPS (переменные читаются в `main.py:202-209`). Если
переменные не заданы — используются дефолты (`TEMPERATURE=0`, `REASONING_EFFORT=`
пусто → без reasoning, `CHUNK_MAX_CHARS=60000`).

### 5.2 Конфиги в репозитории

Коммитятся и правятся через git: `SCHEDULE.txt`, `PROMPT.txt`, `EXCLUDED_USERS.txt`,
`PRIORITY_USERS.txt`. `MODEL_CONFIG.txt` в репозитории **отсутствует** и опционален:
при отсутствии `USE_HTML_EXPORT` дефолтится в `true` (HTML-экспорт), а `MODEL` берётся из
`GEMINI_MODEL` (`main.py:580-588`, `616-617`). Если нужно управлять `USE_HTML_EXPORT`
через git — файл надо добавить в репозиторий (см. «Файлы»). Интерактивные команды в
Actions недоступны — расписание меняется редактированием `SCHEDULE.txt` и push.

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
  коммитить (или использовать private).
- **Лимиты минут**: private — 2000 мин/мес бесплатно; прогон 1–3 мин, при `*/5` это
  ~8640 запусков/мес → **превысит лимит private**. Варианты:
  - public-репозиторий (бесплатно без лимита минут);
  - реже опрашивать (например, `*/15`) и/или запускать только в окнах около времени из
    `SCHEDULE.txt`;
  - **предпочтительно:** генерировать cron-шаблон под конкретные слоты расписания
    (например, 06:00, 06:15, 06:50, 06:55, 07:15 МСК → UTC-эквиваленты), а `--due`
    оставить как страховку.
- **Единственная сессия**: не запускать VPS и Actions одновременно.
- **StringSession** требует перевыпуска при ревоке/смене пароля.
- **Секреты** не логировать; `private.txt` в Actions не нужен.
- **Видимость репозитория не зафиксирована**: для `Hohlas/ChatSum` нужно проверить
  public/private. Если private — лимит 2000 мин/мес актуален и `*/5` его превысит;
  если решено делать public — secrets и ветка `state` станут видимы всем (ограничить
  доступ к `GOOGLE_API_KEY*`/`TELEGRAM_SESSION` нельзя). Проверить перед выбором варианта.

## Порядок работ

0. Разобраться с импортными зависимостями и error propagation (К1, К2): решить,
   добавляем ли `apscheduler` в `requirements.txt` или изолируем его импорт в VPS-ветку;
   определить контракт «успех/ошибка» для записи в state и «красного» run.
1. Import-safety `main.py` (+ `load_env_config`, вынос печати/`exit(1)`).
2. StringSession в `main.py` (+ `gen_session.py`).
3. `run_once.py` (CLI, `--due`, дедуп через `state.json`).
4. Сохранение state в ветку `state`.
5. `.github/workflows/summarize.yml`.
6. Ограничить cron слотами расписания (оптимизация лимитов).
7. README.
8. Тесты из Фазы 7.

## Файлы

- изменяются: `main.py`, `requirements.txt` (см. К1: добавить `apscheduler` либо оставить
  без изменений при изоляции импорта — решение шага 0)
- новые: `run_once.py`, `gen_session.py`, `.github/workflows/summarize.yml`, ветка `state`
- опционально новый: `MODEL_CONFIG.txt` (если нужно управлять `USE_HTML_EXPORT` через git;
  сейчас файла в репозитории нет — при отсутствии используется HTML-экспорт по умолчанию)
- без изменений: `SCHEDULE.txt`, `PROMPT.txt`, `telegram-bot.service.example`
