# ChatSum GitHub Actions — E2E Runbook для следующего агента

> Репозиторий: `Hohlas/ChatSum` (public). Workflow: `ChatSum scheduled summaries` (`.github/workflows/summarize.yml`). Ветка состояния: `state` (только `state.json`). Исполнитель: `run_once.py --due` по cron `*/5`.

---

## Текущее состояние (2026-10-02)

- Ветка `state` на GitHub **отсутствует** (`git ls-remote origin state` → пусто). Первый прогон создаст её через `--orphan`.
- На GitHub **нет secrets / variables** (пусто). Нужен полный набор (см. ниже).
- Локальный `private.txt` — это шаблон (placeholder). Реальные креды только у пользователя.
- Исправление 2.3 (`_is_actions_mode`) в `main.py` **не закоммичено** (находится в рабочей копии). До E2E нужно закоммитить/запушить.
- Тесты: `test_run_once.py` 8/8 PASS на Python 3.11 и 3.14. `py_compile` OK. YAML валиден.

---

## E2E Runbook (выполнить после push)

```bash
cd /home/hohla/git/ChatSum

# 0) Запушить исправление 2.3 (_is_actions_mode) — иначе раннер работает со старым кодом
git add main.py run_once.py plans/ && \
git commit -m "fix: distinguish empty vs unset TELEGRAM_SESSION (2.3)" && \
git push

# 1) Сгенерировать StringSession (требует реальные API_ID/HASH/PHONE в private.txt)
python3 gen_session.py
# → скопировать вывод строки (длинная base64)

# 2) Секреты (repo-scoped)
gh secret set TELEGRAM_API_ID    --repo Hohlas/ChatSum
gh secret set TELEGRAM_API_HASH  --repo Hohlas/ChatSum
gh secret set TELEGRAM_PHONE     --repo Hohlas/ChatSum
gh secret set TELEGRAM_SESSION   --repo Hohlas/ChatSum   # вывод gen_session.py
gh secret set GOOGLE_API_KEY     --repo Hohlas/ChatSum
# опционально: GOOGLE_API_KEY1..3

# 3) Переменные (публичные, repo variables)
gh variable set GEMINI_MODEL            --body "gemini-2.5-flash" --repo Hohlas/ChatSum
gh variable set GEMINI_TEMPERATURE      --body "0"                --repo Hohlas/ChatSum
gh variable set GEMINI_REASONING_EFFORT --body "none"             --repo Hohlas/ChatSum
gh variable set GEMINI_CHUNK_MAX_CHARS  --body "60000"            --repo Hohlas/ChatSum

# 4) Ручной прогон
gh workflow run "ChatSum scheduled summaries" --repo Hohlas/ChatSum
# мониторинг
sleep 20 && gh run watch --repo Hohlas/ChatSum
```

---

## Критерии приёмки E2E

| Проверка | Ожидаемое |
|---|---|
| Workflow run | **Зелёный** (`conclusion: success`) |
| Логи `run_once.py` | Видно окно `[now-15min, now] MSK`, dedup ключи `chat_id\|HH:MM\|period[+/-]` |
| Ветка `state` | После 1-го прогона `git ls-remote origin state` → **SHA** (раньше пусто) |
| Повтор в окне | `gh workflow run ...` повторно → в логах `⏭️ Уже выполнено: <key> (<date>)` |
| Публикация | Сообщение/Telegraph появилось в `TELEGRAM_GROUP_ID` (или «Избранное») |
| Повтор за пределами окна | Следующий cron-цикл → задача выполняется снова |

---

## Важные нюанты для следующего агента

- **Профиль раннера определяется `_is_actions_mode()`** (`'TELEGRAM_SESSION' in os.environ`). Пустой секрет → Actions-профиль (StringSession, ошибка preflight), unset → VPS (файловая сессия, `private.txt`).
- **dedup-ключ**: `chat_id\|HH:MM\|period[+][-]` (порядок суффиксов: `+` затем `-`). `date_key` = дата возникновения слота в MSK.
- **`state.json` хранится в `$RUNNER_TEMP`** → `cp` после `checkout -B state` → `git add -f` → push с rebase-ретраем (см. workflow lines 90-94).
- **Python**: workflow таргетит 3.11; локально тестировалось на 3.11.9 и 3.14.4.
- **Секреты**: для пуш в `state` используется `GITHUB_TOKEN` (`permissions: contents: write`). `workflow` scope **не нужен** (SSH push).
- **Параллельные запуски**: `concurrency: group: chatsum, cancel-in-progress: false` — ставятся в очередь.
- **Ожидаемые секреты/переменные** см. таблицу ниже.

---

## Таблица секретов и переменных

| Тип | Имя | Источник | Обязателен |
|---|---|---|---|
| Secret | `TELEGRAM_API_ID` | `private.txt` | ✅ |
| Secret | `TELEGRAM_API_HASH` | `private.txt` | ✅ |
| Secret | `TELEGRAM_PHONE` | `private.txt` | ✅ |
| Secret | `TELEGRAM_SESSION` | `python gen_session.py` | ✅ |
| Secret | `TELEGRAM_GROUP_ID` | `private.txt` | (опц., иначе «Избранное») |
| Secret | `GOOGLE_API_KEY` | `private.txt` | ✅ |
| Secret | `GOOGLE_API_KEY1..3` | `private.txt` | (опц.) |
| Variable | `GEMINI_MODEL` | `gemini-2.5-flash` | ✅ |
| Variable | `GEMINI_TEMPERATURE` | `0` | ✅ |
| Variable | `GEMINI_REASONING_EFFORT` | `none` | ✅ |
| Variable | `GEMINI_CHUNK_MAX_CHARS` | `60000` | ✅ |

---

## Команды диагностики после прогона

```bash
# Проверить ветку state
git ls-remote origin state

# Посмотреть state.json
git show origin/state:state.json

# Логи последнего прогона
gh run view $(gh run list --workflow summarize.yml --limit 1 --json databaseId -q '.[0].databaseId') --repo Hohlas/ChatSum --log
```

---

> **Статус репозитория на момент передачи:** `HEAD = 36fe5fe` (workflow уже на remote). Исправление 2.3 (`_is_actions_mode`) в рабочей копии, **не закоммичено** — требует `git add main.py run_once.py plans/ && git commit && git push` перед E2E.
