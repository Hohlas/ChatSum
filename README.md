# ChatSum

Telegram userbot для сбора сообщений из чата и создания саммари через Google Gemini.

Бот умеет:
- собирать сообщения за период или по количеству;
- собирать отдельные диапазоны сообщений по номеру или времени;
- фильтровать шум и исключенных пользователей;
- делать AI-саммари командой `/sum`;
- экспортировать JSON без AI командой `/copy`;
- публиковать результат в Telegraph или отправлять HTML-файл в Telegram;
- использовать несколько Google API ключей по кругу.

## Что нужно понимать заранее

- Это **userbot**, а не BotFather-бот.
- Команды вы отправляете **от своего аккаунта Telegram**.
- При первом запуске Telethon попросит код подтверждения.
- Для Gemini на free tier есть жесткие квоты. Большие чаты часто упираются именно в них.

## Быстрый старт

Если хотите просто завести бота без лишней теории:

1. Установите `python3` и `venv`.
2. Клонируйте репозиторий.
3. Создайте виртуальное окружение.
4. Установите зависимости.
5. Заполните `private.txt`.
6. Запустите `python3 main.py`.
7. В Telegram отправьте в нужный чат `/sum 12h`.

Шаги 3–4 делает за вас `./scripts/setup.sh`.
А если нужен бесплатный GitHub Actions вместо VPS — раздел
«Запуск на GitHub Actions → Форк за 10 минут».

Ниже все то же самое, но подробно.

## Установка для новичков

### 1. Установите Python

Нужен Python 3.10+.

Проверка:

```bash
python3 --version
```

Если команда не найдена:

- Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

### 2. Скачайте проект

```bash
git clone https://github.com/Hohlas/ChatSum.git
cd ChatSum
```

### 3. Создайте виртуальное окружение

```bash
python3 -m venv venv
source venv/bin/activate
```

Если все хорошо, в начале строки терминала появится `(venv)`.

### 4. Установите зависимости

```bash
pip install -r requirements.txt
```

## Какие ключи нужны

### Telegram API

Нужно получить:
- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `TELEGRAM_PHONE`

Как получить:

1. Откройте https://my.telegram.org/auth
2. Войдите по номеру телефона.
3. Откройте `API development tools`.
4. Создайте приложение.
5. Скопируйте `api_id` и `api_hash`.

### Google Gemini API

Нужен хотя бы один ключ Google AI Studio.

Как получить:

1. Откройте https://aistudio.google.com
2. Создайте API key.
3. Скопируйте его в `private.txt`.

Можно использовать несколько ключей:
- `GOOGLE_API_KEY`
- `GOOGLE_API_KEY1`
- `GOOGLE_API_KEY2`
- `GOOGLE_API_KEY3`

Бот умеет:
- брать следующий ключ для каждого нового `/sum`;
- переключаться на следующий ключ после ошибок квоты/доступа.

Важно:
- это помогает только если ключи реально имеют **раздельные квоты**;
- если ключи относятся к одному Google project, квоты часто общие.

## Настройка `private.txt`

При первом запуске бот сам создаст `private.txt` из `private.txt.example`.

Но проще сразу сделать вручную:

```bash
cp private.txt.example private.txt
```

Откройте `private.txt` и заполните.

Минимальный рабочий пример:

```env
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
TELEGRAM_PHONE=+79991234567

GOOGLE_API_KEY=AIzaSyExampleKey
GEMINI_MODEL=gemini-3.6-flash

TELEGRAM_GROUP_ID=-1001234567890
```

Расширенный пример:

```env
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
TELEGRAM_PHONE=+79991234567

TELEGRAM_GROUP_ID=-1001234567890

GOOGLE_API_KEY=AIzaSyMainKey
GOOGLE_API_KEY1=AIzaSySecondKey
GOOGLE_API_KEY2=AIzaSyThirdKey
GOOGLE_API_KEY3=AIzaSyFourthKey

GEMINI_MODEL=gemini-3.6-flash
GEMINI_REASONING_EFFORT=medium
GEMINI_CHUNK_MAX_CHARS=70000
```

### Что означают переменные

- `TELEGRAM_API_ID` — Telegram API ID.
- `TELEGRAM_API_HASH` — Telegram API hash.
- `TELEGRAM_PHONE` — ваш номер телефона в международном формате.
- `TELEGRAM_GROUP_ID` — куда отправлять результат.
  Если не задано, бот использует `Избранное`.
- `GOOGLE_API_KEY` — основной Gemini API ключ.
- `GOOGLE_API_KEY1..N` — дополнительные ключи для ротации.
- `GEMINI_MODEL` — модель Gemini.
- `GEMINI_REASONING_EFFORT` — `none`, `low`, `medium`, `high`.
- `GEMINI_CHUNK_MAX_CHARS` — лимит входных символов на один чанк.

### Что выбрать по умолчанию

Если не хотите разбираться:

```env
GEMINI_MODEL=gemini-3.6-flash
GEMINI_REASONING_EFFORT=low
GEMINI_CHUNK_MAX_CHARS=70000
```

Если хотите меньше чанков и меньше API-вызовов:

```env
GEMINI_CHUNK_MAX_CHARS=150000
```

Но имейте в виду:
- чем больше чанк, тем меньше запросов;
- но тем сильнее модель может “схлопывать” темы и делать саммари менее подробным.

## Первый запуск

```bash
python3 main.py
```

Что будет:

1. Бот прочитает `private.txt`.
2. Проверит Telegram API.
3. Проверит Google API ключи.
4. Подключится к Telegram.
5. При первом запуске попросит код подтверждения.

Если все хорошо, в терминале будет список команд и текущая конфигурация Gemini.

## Как пользоваться

### `/sum` — AI-саммари

Примеры:

```text
/sum
/sum 12h
/sum 2d
/sum 3d 6h
/sum 50
/sum 600-800
/sum 2d-3d
/sum 12h-24h
/sum 3-5d
/sum 2-4h
```

Что это значит:
- `/sum` — последние 24 часа;
- `/sum 12h` — последние 12 часов;
- `/sum 2d` — последние 2 дня;
- `/sum 3d 6h` — последние 3 дня и 6 часов;
- `/sum 50` — последние 50 сообщений;
- `/sum 600-800` — текстовые сообщения с 600-го по 800-е от конца чата;
- `/sum 2d-3d` — сообщения от 3 до 2 дней назад;
- `/sum 12h-24h` — сообщения от 24 до 12 часов назад.

Диапазоны удобны для диагностики проблемных фрагментов: сначала можно проверить крупный кусок, затем делить его пополам.

### `/copy` — экспорт JSON без AI

Примеры:

```text
/copy 12h
/copy 2d
/copy 100
/copy 600-800
/copy 2d-3d
```

Это полезно, если:
- Gemini уперся в квоты;
- хотите анализировать вручную в другом ИИ;
- хотите сохранить сырой экспорт.

`/copy` поддерживает те же диапазоны, что и `/sum`, но не тратит токены Gemini.

## Что приходит на выходе

### Режим HTML

Если включен `USE_HTML_EXPORT=true` в `MODEL_CONFIG.txt`, бот:
- отправляет короткое сообщение со статистикой;
- создает HTML-файл;
- отправляет HTML-файл в Telegram.

### Режим Telegraph

Если включен `USE_HTML_EXPORT=false`, бот:
- публикует результат в Telegraph;
- отправляет ссылку в Telegram;
- для больших саммари может публиковать несколько частей.

### Что попадает в статистику

Обычно бот пишет:
- модель;
- сколько сообщений обработано;
- сколько найдено тем;
- сколько найдено URL;
- период анализа;
- сколько токенов потрачено;
- ошибки API, если были.

Для явного диапазона бот сохраняет диапазон в статистике. Например:

```text
• Обработано: 600-800 сообщений = 22 Тем
```

Если большое саммари разбито на несколько публикаций, в заголовке каждой страницы Telegraph/HTML указывается часть и диапазон сообщений, например:

```text
Часть 1 (сообщения 1-516)
Часть 2 (сообщения 493-600)
```

## Команды управления

### Основные

```text
/config
/show_prompt
/show_excluded
/show_priority
/show_model
/reload_config
```

### Списки пользователей

```text
/add_excluded User Name
/remove_excluded User Name
/add_priority User Name
/remove_priority User Name
```

### Смена модели

```text
/set_model gemini-3.6-flash
```

Команда обновляет `GEMINI_MODEL` в `private.txt`.

## Конфигурационные файлы

### `private.txt`

Главный файл конфигурации:
- Telegram API;
- Google API keys;
- Gemini model;
- reasoning;
- chunk size;
- ID группы для результатов.

### `EXCLUDED_USERS.txt`

Пользователи, которых нужно исключать из анализа.

Пример:

```txt
# Исключенные пользователи
SpamBot
Flooder
```

Важно:
- указывать нужно **отображаемое имя в чате**, не username.

### `PRIORITY_USERS.txt`

Пользователи, чьи сообщения модель должна считать приоритетными.

Пример:

```txt
# Приоритетные пользователи
Lex
Sergey (ICO Drops)
```

### `PROMPT.txt`

Промпт для Gemini.

Если хотите:
- больше тем;
- меньше схлопывания;
- более подробные цитаты;

редактируйте именно этот файл.

### `MODEL_CONFIG.txt`

Этот файл все еще используется, но теперь его роль уже, чем раньше.
При этом он может вообще отсутствовать: бот умеет стартовать и без него.

Сейчас он в основном хранит:
- режим экспорта `USE_HTML_EXPORT`;
- старые совместимые флаги интерфейса.

Пример:

```txt
MODEL=gemini-3.6-flash
USE_REASONING=false
USE_HTML_EXPORT=true
```

Важно:
- реальная модель для Gemini берется из `private.txt` (`GEMINI_MODEL`);
- reasoning для Gemini тоже берется из `private.txt` (`GEMINI_REASONING_EFFORT`);
- `USE_HTML_EXPORT` по-прежнему читается из `MODEL_CONFIG.txt`.
- если `MODEL_CONFIG.txt` отсутствует, бот использует дефолтный экспорт в HTML.

## Формат JSON экспорта

`/copy` создает flat JSON.

Пример:

```json
{
  "metadata": {
    "chat_id": "1675726024",
    "period_start": "2026-05-04 03:00:00",
    "chat_name": "ProChat",
    "export_date": "2026-05-04 05:30:00",
    "total_messages": 120,
    "filtered_messages": 95
  },
  "messages": [
    {
      "id": 243613,
      "s": "Insey",
      "t": "Текст сообщения"
    },
    {
      "id": 243616,
      "s": "Artem",
      "t": "Ответ на сообщение",
      "r": 243613
    }
  ]
}
```

Где:
- `s` — sender;
- `t` — text;
- `r` — reply_to message id.

## Ограничения и реальные проблемы

### 1. Квоты Gemini

Самая частая проблема сейчас — это не баг бота, а квоты Google.

В логах это выглядит так:

```txt
HTTP 429
Quota exceeded
RESOURCE_EXHAUSTED
```

Что делает бот:
- показывает короткую ошибку API;
- при quota exceeded перестает долбить следующие чанки;
- может переключиться на следующий Google API key, если он есть.

### 2. Большие чаты

Если сообщений очень много:
- бот разобьет их на чанки;
- каждый чанк — отдельный запрос к Gemini;
- больше чанков = выше расход квоты.

### 3. Большой `GEMINI_CHUNK_MAX_CHARS`

Если сильно увеличить лимит чанка:
- плюсы: меньше запросов;
- минусы: саммари может стать менее подробным.

## Установка как сервис

### Вариант 1. Просто запустить вручную

```bash
cd ChatSum
source venv/bin/activate
python3 main.py
```

### Вариант 2. Через `screen`

```bash
./scripts/start_bot_background.sh
screen -r telegram-chat-analyzer
```

Отключиться от `screen`, не останавливая процесс:
- `Ctrl+A`
- потом `D`

Остановить:

```bash
./scripts/stop_bot.sh
```

### Вариант 3. Через `systemd`

Пример юнита:

```ini
[Unit]
Description=ChatSum Telegram userbot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/ChatSum
ExecStart=/path/to/ChatSum/venv/bin/python /path/to/ChatSum/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Дальше:

```bash
sudo systemctl daemon-reload
sudo systemctl enable telegram-bot
sudo systemctl start telegram-bot
sudo systemctl status telegram-bot
```

## Запуск на GitHub Actions

Тот же код может работать на GitHub Actions вместо VPS: ежедневные саммари из
`SCHEDULE.txt` запускаются по cron (`*/10`, UTC), раннер сам определяет, какие
задания «должны» в МСК, и не повторяет уже выполненные (дедупликация через
ветку `state`). Разовые команды тоже есть: пересланное сообщение + `sum50`
в General канала результатов выполняется следующим раном (опрос inbox,
без долгоживущего процесса).

### Форк за 10 минут

```bash
# 1. Форк кнопкой Fork на GitHub, затем клонируйте СВОЙ форк:
git clone https://github.com/<ВЫ>/ChatSum.git
cd ChatSum
# 2. Форк должен быть PUBLIC (Settings → General → Change visibility):
#    на private-репо cron быстро съест бесплатные минуты Actions.
./scripts/setup.sh            # venv + зависимости + заготовка private.txt
# ... заполните private.txt (ключи: my.telegram.org, aistudio.google.com)
./venv/bin/python tools/gen_session.py   # один раз: ввести код из Telegram
gh auth login               # один раз: доступ gh к вашему форку
./scripts/push_github_secrets.sh  # секреты и переменные — в ваш репозиторий
```

Что делают скрипты:

- `scripts/setup.sh` — проверяет Python 3.10+, создаёт/чинит `venv`, ставит
  `requirements.txt`, создаёт `private.txt` из примера, проверяет наличие `gh`.
- `tools/gen_session.py` — печатает StringSession **и** сохраняет её
  в `telegram_session.txt` (gitignored) для следующего шага.
- `scripts/push_github_secrets.sh` — читает `private.txt` и сессию, заливает Secrets
  (`TELEGRAM_*`, `GOOGLE_API_KEY*`) и Variables (`GEMINI_*`) в репозиторий
  из git remote (переопределить: `--repo OWNER/REPO`; предпросмотр:
  `--dry-run`). Все `GOOGLE_API_KEY<N>` забираются сколько бы их ни было
  (1 или 20+) + собирается сводный `GOOGLE_API_KEYS` без потолка.
  Сессия берётся из `--session`, env `TELEGRAM_SESSION`,
  `telegram_session.txt` или интерактивного ввода. Значения никогда не печатаются.

Дальше: поправьте `SCHEDULE.txt`, запушьте в `main` и запустите вручную
из `Actions → ChatSum scheduled summaries → Run workflow` (или дождитесь
ближайшего cron-запуска). Разовые команды без расписания:
- в General: `sum50 t.me/chatname`, `sum1d @chatname` или
  `sum100 https://t.me/c/1234567890/99` (закрытые чаты — второй формой);
- прямо в топике нужного чата: просто `sum50` (источник = чат топика).
Выполненная команда удаляется; мусор и опечатки игнорируются и остаются.

### Выбор площадки

| | VPS | GitHub Actions |
|---|---|---|
| Точка входа | `python3 main.py` (долгоживущий процесс) | `run_once.py --watch` (cron) |
| Сессия | файловая `session_name.session` | StringSession в секрете `TELEGRAM_SESSION` |
| Команды | `/sum`, `/copy`, `/config` вживую | `sum`/`copy` через inbox-форвард (опрос), `/config` нет |
| Стоимость | сервер | бесплатно на public-репо |

⚠️ **Используйте одну площадку за раз.** Одновременная работа двух процессов
с одной сессией Telegram не поддерживается: возможен рассинхрон состояния
апдейтов и разрыв авторизации.

### Настройка вручную (без скриптов)

Если `scripts/push_github_secrets.sh` не подходит:

1. Проверьте visibility репозитория: для `schedule`-триггеров cron и
   бесплатных минут подходит **public**-репозиторий (`Settings → General →
   Danger Zone`).
2. В `Settings → Secrets and variables → Actions → New repository secret`
   добавьте (обязательные — `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`,
   `TELEGRAM_PHONE`, `TELEGRAM_SESSION`, `GOOGLE_API_KEY`):
   - `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` — из my.telegram.org;
   - `TELEGRAM_PHONE` — номер аккаунта;
   - `TELEGRAM_SESSION` — сгенерируйте один раз локально:
     ```bash
     ./venv/bin/python tools/gen_session.py
     ```
     Скопируйте вывод (длинная строка) в секрет `TELEGRAM_SESSION`.
     Не коммитьте эту строку. Перевыпускается, если пароль/сессия были изменены.
   - `TELEGRAM_GROUP_ID` — (необязательно) канал результатов, иначе «Избранное»;
   - `GOOGLE_API_KEY[,1..N]` — ключи Gemini.
3. В `Settings → Secrets and variables → Actions → New repository variable`
   настройте `GEMINI_MODEL`, `GEMINI_TEMPERATURE`,
   `GEMINI_REASONING_EFFORT` (например, `none`), `GEMINI_CHUNK_MAX_CHARS`.
   Обратите внимание: **Variables публичны**, секреты — нет. Если значения
   `GEMINI_*` надо скрыть, кладите в Secrets, а не в Variables.
   Рекомендуется явно задать `GEMINI_REASONING_EFFORT`, иначе в логах будет
   предупреждение на каждый запуск генерации.
4. Отредактируйте `SCHEDULE.txt` (коммитите через git) и запушите в `main`.
5. Запустите вручную из `Actions → ChatSum scheduled summaries →
   Run workflow`, либо дождитесь ближайшего cron-запуска.

### Как это работает

- `.github/workflows/summarize.yml` ставит env из Secrets/Variables и вызывает
  `python run_once.py --watch` (дежурный цикл до 5 ч: due-задачи + опрос inbox
  каждые ~30 сек; cron `*/11`).
- `run_once.py` читает `SCHEDULE.txt`, `PROMPT.txt`, `EXCLUDED_USERS.txt`,
  `PRIORITY_USERS.txt` из репозитория; `MODEL_CONFIG.txt` в репозитории нет —
  при отсутствии используется HTML-экспорт по умолчанию, а модель берётся из
  `GEMINI_MODEL`. Если нужно управлять `USE_HTML_EXPORT` через git — добавьте
  `MODEL_CONFIG.txt` в репозиторий.
- Inbox: сообщение вида `sum50`/`copy1d` в General канала результатов
  выполняется ближайшим дежурным через то же ядро, что VPS-команды.
  Провал анализа — до 5 попыток (команда не удаляется, счётчик в state),
  смерть процесса посреди анализа — тоже повтор следующим опросом.
  Источник берётся из ссылки `t.me/...`, из @username, из названия чата,
  упомянутого в команде, либо из топика (топик-команда).
- «Должные» задания: слот due, если в state нет отметки за дату его
  последнего наступления (МСК) — опоздание любой длины (простой раннера,
  задержка cron) догоняется одним разом, а не теряется. Падающий 3 раза
  подряд слот пропускается на 3 часа (защита квоты Gemini).
- Дежурство: cron `*/11`, watch до 5 ч; роли лидер/standby через флаги
  `leader.json`/`standby.json` в ветке `state`. Упавший лидер заменяется
  standby за минуты; молчаливый standby/уходящий лидер state не затирают
  (pull-merge перед финальным save).
- Состояние хранится в ветке `state` (только `state.json` + флаги) и
  переиспользуется между прогонами.
- `run_once.py` возвращает ненулевой код, если хотя бы одна должная задача
  упала, — такой прогон виден как красный в Actions, а неуспешная задача
  остаётся незаписанной в state и будет повторена.

### Дополнительно

- Для cron на public-репозитории: если 60 дней не было активности,
  GitHub отключает `schedule` — периодически коммитьте.
- Частота опроса задаётся флагами `--poll-interval`/`--watch-seconds`
  (дефолты 30 с / 5 ч) и inputs workflow_dispatch.

## Частые проблемы

### Бот не запускается

Проверьте:
- установлен ли Python;
- активировано ли `venv`;
- выполнен ли `pip install -r requirements.txt`;
- заполнен ли `private.txt`.

### Telegram просит заново авторизоваться

Удалите старую сессию и запустите заново:

```bash
rm session_name.session
python3 main.py
```

### Ошибка API key

Проверьте:
- нет ли пробелов в начале или конце;
- не вставился ли ключ с переносом строки;
- ключ действительно создан в Google AI Studio.

### `429 quota exceeded`

Это значит:
- либо кончился бесплатный лимит модели;
- либо у ключа вообще нет доступной free-tier квоты для этой модели;
- либо у всех ключей из ротации общая квота и она уже выработана.

Что можно сделать:
- уменьшить период анализа;
- уменьшить число чанков;
- увеличить `GEMINI_CHUNK_MAX_CHARS`;
- добавить независимые ключи;
- перейти на платный tier.

### Саммари слишком короткое

Попробуйте:
- уменьшить `GEMINI_CHUNK_MAX_CHARS`;
- понизить `GEMINI_REASONING_EFFORT`;
- сделать промпт более анти-схлопывающим.

### Саммари слишком грубое или плохо ужимает цитаты

Попробуйте:

```env
GEMINI_REASONING_EFFORT=medium
```

## Проверка зависимостей

`requirements.txt` сейчас соответствует коду.

Используются:
- `telethon`
- `openai`
- `python-dotenv`
- `httpx`
- `telegraph`
- `apscheduler` (планировщик ежедневных саммари и `run_once.py`)

> `run_once.py` (GitHub Actions) импортирует `main`, поэтому `apscheduler`
> обязателен в `requirements.txt` для обеих площадок.

## Безопасность

- Никогда не коммитьте `private.txt`.
- Не коммитьте `*.session`.
- Не публикуйте API-ключи в скриншотах и логах.
- Если ключ утек, перевыпустите его.

## Лицензия

[MIT](LICENSE)
