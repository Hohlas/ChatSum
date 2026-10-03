# ChatSum GitHub Actions — финальный план (замороженные решения) + E2E runbook

> Репозиторий: `Hohlas/ChatSum` (public). Workflow: `ChatSum scheduled summaries` (`.github/workflows/summarize.yml`). Ветка состояния: `state` (только `state.json`). Исполнитель: `run_once.py`.
> Замороженные решения (§0) согласованы с владельцем и не пересматриваются исполнителем.

## 0. Замороженные решения

1. Репозиторий **public**. Причина: бюджет 0 ₽; частые idle-раны съедают private-квоту (2000 мин Free), на public standard-раннеры бесплатны и безлимитны.
2. Каденс: cron **`*/10 * * * *`** (UTC, было `*/5` — поменять), job `timeout-minutes: 12`, `concurrency: {group: chatsum, cancel-in-progress: false}`. Внутри рана — цикл **~8 мин с шагом 60 сек** (due-задачи + опрос inbox, см. §1).
3. On-demand команды `/sum`/`/copy` — через **опрос, а не events**: долгоживущего слушателя в Actions нет. Inbox = тема **General** приватного канала результатов (`TELEGRAM_GROUP_ID`). Проверки sender **нет** (в тему допущен друг владельца).
4. Дедуп inbox-команд через `state.json` **не нужен**: обработанная команда **удаляется** (`delete_messages`), повторный опрос её не видит. Раны сериализованы (п.2) — двойной обработки нет.
5. Новую тему не заводить, использовать существующую `General`. ID чатов в команде не требуются (источник берётся из форварда, §2).
6. `setup-python` — с `cache: 'pip'` + `cache-dependency-path: 'requirements.txt'` (срезает оверхед холодного старта).
7. Секреты — только Secrets/Variables (таблица §4). В логах — только маскированные ключи (`mask_api_key`), полные значения не печатать.

## 1. Режим работы рана (что реализовать)

Новый режим `run_once.py --watch --watch-seconds 480 --poll-interval 60` (имена флагов точные; дефолты: 480 / 60):

```
deadline = now + watch_seconds
loop:
  run_due_once()   # существующая логика --due: окно [now-LAG_MAX, now] MSK, LAG_MAX=15, dedup по state.json (без изменений)
  poll_inbox_once() # §2, обработка всех новых команд, удаление обработанных
  if now >= deadline: break
  sleep(poll_interval)
disconnect clients один раз в конце (а не на каждой итерации)
```

`--due`, `--chat-id`, `--list` — без изменений. Workflow вызывает именно `--watch`. Повторный `run_due` внутри цикла безопасен (dedup-ключи `chat_id|HH:MM|period[+-]` уже есть).

## 2. Inbox: форвард + команда в General канала результатов

UX: пользователь пересылает **любое** сообщение из чата-источника в General канала результатов и тем же сообщением (подпись к форварду) либо ответом на него пишет команду:

```
sum100 | sum100-800 | sum12h | sum1d | copy100 | copy1d ... [+/-]
```

Грамматика команды = грамматика VPS-команд `/sum|/copy` один в один (включая голую команду без аргументов = дефолт 24h, мультипараметры `1d 6h`, формы `3-5d`, суффиксы `+`/`-` с пробелом или без). Отдельной inbox-грамматики нет: парсинг один, общий (см. п.2).

Резолв источника (порядок строгий):

1. Сообщение имеет `fwd_from.from_id` (PeerChannel/PeerChat) → `source_chat_id` = он.
2. Иначе сообщение — reply (`reply_to_msg_id`) → получить родителя; если у родителя есть `fwd_from.from_id` → `source_chat_id` = он.
3. Иначе — не команда, пропустить (не удалять).

Обработка команды:

1. Распарсить текст той же логикой, что VPS-обработчик `process_chat_command` (`main.py:3419`): `use_ai` (sum=True/copy=False), `hours/days/limit/range_start/range_end/time_range_start/time_range_end`, `post_to_source/post_as_telegram`. Для этого вынести парсинг из `process_chat_command` в чистую функцию `parse_chat_command_args(text) -> (...)` и использовать её в обоих местах (VPS-хендлеры `handle_sum_command`/`handle_copy_command` переключить на неё же, поведение не менять).
2. Ядро выполнения — **`run_analysis`** (`main.py:2870`), НЕ `scheduled_analysis_job`: только `run_analysis` принимает `limit/range/time_range` и `use_ai=False` для copy. `scheduled_analysis_job` (`main.py:3663`) понимает лишь период `\d+[hd]` и всегда `use_ai=True` — остаётся только для due-задач (§1). Вызов: резолв `chat_name` через `get_entity(source_chat_id)` (как в `scheduled_analysis_job`), затем `run_analysis(source_chat_id, chat_name, hours=..., days=..., limit=..., range_start=..., range_end=..., time_range_start=..., time_range_end=..., use_ai=..., post_to_source=..., post_as_telegram=..., scheduled=False)`.
3. Результат постится по действующим правилам (`RESULTS_DESTINATION`, топик источника через `get_or_create_topic` — без изменений; `run_analysis` это уже делает сам).
4. После успеха — **удалить командное сообщение** (`delete_messages`, best-effort: неуспех удаления не роняет ран). При неуспехе анализа — сообщение НЕ удалять (будет повторено следующим опросом), в топик источника — короткое сообщение об ошибке (существующий error-path `run_analysis`).
5. Опрос = `iter_messages(RESULTS_DESTINATION, min_id=last_seen)` где `last_seen` — in-memory максимум за ран (персистить не надо: неудалённые = необработанные).

## 3. Изменения workflow (`summarize.yml`)

- cron `*/10 * * * *` (замена `*/5`); `workflow_dispatch` оставить (ручной прогон идёт через `--chat-id`, без изменений).
- `setup-python`: `python-version: '3.11'`, добавить `cache: 'pip'`, `cache-dependency-path: 'requirements.txt'`.
- `timeout-minutes: 12` на job; `STATE_PATH`/`Fetch state`/`Persist state` — без изменений (ветка `state` только для due-dedup).
- Вызов: `python run_once.py --watch` (env — тот же блок, без новых переменных).

## 4. Секреты и переменные (без изменений)

Secrets: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_PHONE`, `TELEGRAM_SESSION` (StringSession из `gen_session.py`), `GOOGLE_API_KEY` (+ опц. `GOOGLE_API_KEY1..3`), опц. `TELEGRAM_GROUP_ID`. Variables (публичные): `GEMINI_MODEL=gemini-2.5-flash`, `GEMINI_TEMPERATURE=0`, `GEMINI_REASONING_EFFORT=none`, `GEMINI_CHUNK_MAX_CHARS=60000`. Push в `state` — встроенным `GITHUB_TOKEN` (`permissions: contents: write`).

## 5. Почему внутренний шаг 60 сек (не меньше)

Доминирующая задержка команды — ожидание старта следующего cron-рана (минуты), а не внутренний опрос. Чаще опрос = больше вызовов Telegram API (риск FloodWait) и быстрее сгорание Gemini-квоты при ретраях, выигрыша в latency — ноль. 60 сек достаточно; интервал — флаг, не константа.

## 6. Приёмка

- `python run_once.py --watch --watch-seconds 120 --poll-interval 10` локально (VPS-профиль): due + inbox работают, процесс выходит сам ~через 120 сек, exit 0.
- Тест inbox: форвард из чата из `SCHEDULE.txt` + `sum50` в General → в течение рана появляется саммари, команда удалена; повторный ран её не повторяет.
- Команда без форварда (просто текст) — проигнорирована и не удалена.
- `summarize.yml`: cron `*/10`, pip-cache строки на месте, YAML валиден, `test_run_once.py` PASS.
- Cron-ран зелёный; холостой ран (нет due, нет команд) — success за ~10 мин.

## 7. Известные ограничения (принять)

Джиттер schedule ±2–5 мин (латентность команды = до ~15 мин в худшем случае — в допуске владельца 5–10+ мин); отключение schedule после 60 дней без коммитов (лечится любым коммитом); любой участник General может тратить Gemini-квоту (принято владельцем, лимиты — вне скоупа).

---

## E2E runbook (исходный, актуален с поправкой cron */10)

```bash
cd /home/hohla/git/ChatSum

# 0) Запушить все изменения перед E2E
git add main.py run_once.py plans/ .github/ && git commit -m "..." && git push

# 1) Сгенерировать StringSession (нужны реальные API_ID/HASH/PHONE в private.txt)
python3 gen_session.py   # → длинная base64-строка

# 2) Секреты (repo-scoped)
gh secret set TELEGRAM_API_ID    --repo Hohlas/ChatSum
gh secret set TELEGRAM_API_HASH  --repo Hohlas/ChatSum
gh secret set TELEGRAM_PHONE     --repo Hohlas/ChatSum
gh secret set TELEGRAM_SESSION   --repo Hohlas/ChatSum
gh secret set GOOGLE_API_KEY     --repo Hohlas/ChatSum
# опционально: GOOGLE_API_KEY1..3

# 3) Переменные (публичные)
gh variable set GEMINI_MODEL            --body "gemini-2.5-flash" --repo Hohlas/ChatSum
gh variable set GEMINI_TEMPERATURE      --body "0"                --repo Hohlas/ChatSum
gh variable set GEMINI_REASONING_EFFORT --body "none"             --repo Hohlas/ChatSum
gh variable set GEMINI_CHUNK_MAX_CHARS  --body "60000"            --repo Hohlas/ChatSum

# 4) Ручной прогон + мониторинг
gh workflow run "ChatSum scheduled summaries" --repo Hohlas/ChatSum
sleep 20 && gh run watch --repo Hohlas/ChatSum
```

Критерии: workflow зелёный; в логах окно `[now-15min, now]` MSK; ветка `state` появилась (`git ls-remote origin state` → SHA); повтор в окне → `⏭️ Уже выполнено`; публикация дошла до канала/«Избранного».

---

## 8. Приложение: технические нюансы рантайма (сохранены из runbook 2026-10-02)

Исполнитель: учитывать при правках `run_once.py` / `summarize.yml`.

- **Профиль определяется `_is_actions_mode()`** (`main.py`): `'TELEGRAM_SESSION' in os.environ` → Actions-профиль (StringSession из секрета, валидация через preflight в `run_once.py`); unset → VPS-профиль (файловая сессия `session_name`, конфиг из `private.txt`). Пустой секрет `TELEGRAM_SESSION` = ошибка конфигурации (не молчаливый фолбэк на VPS). Новое наблюдение: fix уже закоммичен (`b88e38c`), перед E2E убедиться, что раннер забрал свежий код.
- **Dedup-ключ due-задач**: `chat_id|HH:MM|period[+][-]` (порядок суффиксов: `+` затем `-`); `date_key` = МСК-дата наступления слота. Парсинг суффиксов — `_parse_suffixes` (общий для `--due` и inbox-команд §2).
- **Механика ветки `state`**: `state.json` живёт в `$RUNNER_TEMP` (вне рабочего дерева, иначе конфликтует с checkout). Fetch: `git fetch origin +refs/heads/state:refs/remotes/origin/state` (явный refspec — shallow checkout сам `origin/state` не создаёт), затем `git show origin/state:state.json`. Persist: `checkout -B state` (или `--orphan` + `git read-tree --empty` при первом запуске, иначе в ветку уедет весь код) → `cp $RUNNER_TEMP/state.json` → `git add -f` → commit → push с rebase-ретраем при non-fast-forward. Успешно выполненные задачи пишутся сразу (частичный провал = зелёный state + красный ран, упавшая задача повторяется).
- **Параллельность**: `concurrency: group: chatsum, cancel-in-progress: false` — раны в очередь, не поверх. Отдельная группа от hype.
- **Права**: push в `state` — встроенным `GITHUB_TOKEN` (`permissions: contents: write`); отдельный PAT/SSH-ключ не нужен, `workflow`-scope не нужен.
- **Python**: workflow — 3.11; `test_run_once.py` ранее гонялся на 3.11 и 3.14 (8/8 PASS) — после правок прогнать заново на том, что доступно.
- **Диагностика после прогона**:
  ```bash
  git ls-remote origin state          # ветка состояния существует
  git show origin/state:state.json   # содержимое dedup-состояния
  gh run view $(gh run list --workflow summarize.yml --limit 1 --json databaseId -q '.[0].databaseId') --repo Hohlas/ChatSum --log
  ```
- **Что проверить при E2E** (на 2026-10-02 было пусто, статус мог измениться): наличие ветки `state` на origin; заполненность Secrets/Variables (§4); `private.txt` локально — только шаблон, реальные креды не коммитить.
