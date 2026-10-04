#!/usr/bin/env bash
# Заливает Secrets и Variables для GitHub Actions из private.txt одной командой.
# Репозиторий определяется из git remote (форк-френдли), можно переопределить --repo.
#
#   ./scripts/push_github_secrets.sh [--repo OWNER/REPO] [--session STRING] [--dry-run]
#
# TELEGRAM_SESSION берётся по приоритету:
#   1. --session "..."   2. env TELEGRAM_SESSION
#   3. telegram_session.txt (создаёт gen_session.py)   4. интерактивный ввод
# Значения секретов НИКОГДА не печатаются (только имена и длины).
set -euo pipefail
cd "$(dirname "$0")/.."   # корень репо (сам скрипт живёт в scripts/)

REPO=""
SESSION_ARG=""
DRY_RUN=0

usage() {
  echo "Использование: $0 [--repo OWNER/REPO] [--session STRING] [--dry-run]"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:?нужен OWNER/REPO}"; shift 2 ;;
    --session) SESSION_ARG="${2:?нужна строка сессии}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "❌ Неизвестный аргумент: $1"; usage; exit 2 ;;
  esac
done

if ! command -v gh >/dev/null; then
  echo "❌ gh не найден. Установка: https://cli.github.com/ , затем: gh auth login"
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "❌ gh не авторизован. Выполните: gh auth login"
  exit 1
fi

if [ -z "$REPO" ]; then
  REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || true)"
  if [ -z "$REPO" ]; then
    # Fallback без API (переживает сбои сети): парсим origin локально.
    origin_url="$(git remote get-url origin 2>/dev/null || true)"
    REPO="$(printf '%s' "$origin_url" | sed -E -e 's#^https?://[^/]*@#https://#' -e 's#^(https?://github.com/|git@github.com:)##' -e 's#\.git$##')"
    case "$REPO" in
      *:*|*@*) REPO="" ;; # не github-remote — не годится
      */*) ;; # похоже на OWNER/REPO — годится
      *) REPO="" ;;
    esac
  fi
  if [ -z "$REPO" ]; then
    echo "❌ Не удалось определить репозиторий."
    echo "--- вывод gh (причина): ---"
    gh repo view --json nameWithOwner -q .nameWithOwner 2>&1 | head -3 || true
    echo "---------------------------"
    echo "Укажите явно: $0 --repo OWNER/REPO"
    exit 1
  fi
fi
echo "Репозиторий: $REPO"

if [ ! -f private.txt ]; then
  echo "❌ Нет private.txt. Сначала: cp private.txt.example private.txt  (или ./scripts/setup.sh) и заполните ключи."
  exit 1
fi

# Значение KEY из private.txt (последнее вхождение, без CR, крайних пробелов
# и хвостового комментария « # ...» — как это делает python-dotenv).
# Всегда exit 0 (grep без совпадений при set -euo pipefail иначе роняет скрипт).
get_var() {
  grep -E "^$1=" private.txt 2>/dev/null | tail -1 | cut -d= -f2- | sed 's/[[:space:]][[:space:]]*#.*$//' | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' || true
}

# Заглушка из примера вместо реального значения?
is_placeholder() {
  case "$1" in
    ""|*ваш_*|*your_*|*\.\.\.*|*Example*|*CHANGEME*|*"<"*|*">"*|*"</"*|*"/"*) return 0 ;;
  esac
  return 1
}

# Источник TELEGRAM_SESSION по приоритету.
SESSION=""
if [ -n "$SESSION_ARG" ]; then
  SESSION="$SESSION_ARG"; SRC="--session"
elif [ -n "${TELEGRAM_SESSION:-}" ]; then
  SESSION="$TELEGRAM_SESSION"; SRC="env TELEGRAM_SESSION"
elif [ -f telegram_session.txt ]; then
  SESSION="$(head -1 telegram_session.txt | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"; SRC="telegram_session.txt"
fi
if is_placeholder "$SESSION"; then
  if [ "$DRY_RUN" = 1 ]; then
    SESSION="dry-run-fake-session"; SRC="заглушка (dry-run)"
  else
    echo "Введите StringSession (из gen_session.py, ввод скрыт):"
    read -rs SESSION < /dev/tty
    echo ""
    SRC="ручной ввод"
  fi
fi
if is_placeholder "$SESSION"; then
  echo "❌ TELEGRAM_SESSION пуст. Сгенерируйте: ./venv/bin/python tools/gen_session.py"
  exit 1
fi
echo "TELEGRAM_SESSION: источник $SRC (длина ${#SESSION})"

gh_set_secret() {  # name value required(1/0)
  local name="$1" value="$2" required="$3"
  if is_placeholder "$value"; then
    if [ "$required" = 1 ]; then
      echo "❌ $name не задан в private.txt (пусто или заглушка). Заполните и повторите."
      exit 1
    fi
    echo "⏭️  $name пропущен (не задан, необязательный)"
    return 0
  fi
  if [ "$DRY_RUN" = 1 ]; then
    echo "DRY secret   $name (длина ${#value})"
  else
    gh secret set "$name" --repo "$REPO" --body "$value" >/dev/null
    echo "✅ secret   $name (длина ${#value})"
  fi
}

gh_set_var() {  # name value
  local name="$1" value="$2"
  if [ "$DRY_RUN" = 1 ]; then
    echo "DRY variable $name=$value"
  else
    gh variable set "$name" --repo "$REPO" --body "$value" >/dev/null
    echo "✅ variable $name=$value"
  fi
}

echo "--- Secrets ---"
gh_set_secret TELEGRAM_API_ID   "$(get_var TELEGRAM_API_ID)" 1
gh_set_secret TELEGRAM_API_HASH "$(get_var TELEGRAM_API_HASH)" 1
gh_set_secret TELEGRAM_PHONE    "$(get_var TELEGRAM_PHONE)" 1
gh_set_secret TELEGRAM_SESSION  "$SESSION" 1
gh_set_secret GOOGLE_API_KEY    "$(get_var GOOGLE_API_KEY)" 1
# Все GOOGLE_API_KEY<N> из private.txt — сколько бы их ни было (1, 5, 20+).
# Имена — по возрастанию N (сортировка числовая: KEY2 < KEY10).
KEY_NAMES="$(grep -oE '^GOOGLE_API_KEY[0-9]+' private.txt 2>/dev/null | awk '{ n=$0; sub(/^GOOGLE_API_KEY/, "", n); printf "%010d %s\n", n+0, $0 }' | sort | cut -d' ' -f2- || true)"
BUNDLE="$(get_var GOOGLE_API_KEY)
"
# shellcheck disable=SC2086  # разбиение на слова здесь намеренно (имена по одному на строке)
for name in $KEY_NAMES; do
  v="$(get_var "$name")"
  if [ -z "$v" ] || is_placeholder "$v"; then
    echo "⏭️  $name пропущен (пусто или заглушка)"
    continue
  fi
  gh_set_secret "$name" "$v" 0
  BUNDLE="$BUNDLE$v
"
done
# Сводный секрет без потолка на количество: бот (load_google_api_keys)
# разберёт по строкам/запятым и уберёт дубликаты. Пустые строки вычищены.
BUNDLE_CLEAN="$(printf '%s' "$BUNDLE" | sed '/^[[:space:]]*$/d')"
if [ "$DRY_RUN" = 1 ]; then
  echo "DRY secret   GOOGLE_API_KEYS (bundle: $(printf '%s' "$BUNDLE_CLEAN" | grep -c . || true) кл., значения скрыты)"
else
  gh secret set "GOOGLE_API_KEYS" --repo "$REPO" --body "$BUNDLE_CLEAN" >/dev/null
  echo "✅ secret   GOOGLE_API_KEYS (bundle: все ключи разом)"
fi
GROUP_ID="$(get_var TELEGRAM_GROUP_ID)"
case "$GROUP_ID" in
  -[0-9]*|[0-9]*)
    if is_placeholder "$GROUP_ID"; then
      echo "⏭️  TELEGRAM_GROUP_ID пропущен (заглушка — будет «Избранное»)"
    else
      gh_set_secret TELEGRAM_GROUP_ID "$GROUP_ID" 0
    fi
    ;;
  *) echo "⏭️  TELEGRAM_GROUP_ID пропущен (не задан — будет «Избранное»)" ;;
esac

echo "--- Variables (публичные; секретные значения кладите в Secrets) ---"
MODEL="$(get_var GEMINI_MODEL)";            [ -n "$MODEL" ] || MODEL="gemini-3.6-flash"
TEMP="$(get_var GEMINI_TEMPERATURE)";       [ -n "$TEMP" ] || TEMP="0"
EFFORT="$(get_var GEMINI_REASONING_EFFORT)"; [ -n "$EFFORT" ] || EFFORT="none"
CHUNK="$(get_var GEMINI_CHUNK_MAX_CHARS)";  [ -n "$CHUNK" ] || CHUNK="60000"
gh_set_var GEMINI_MODEL "$MODEL"
gh_set_var GEMINI_TEMPERATURE "$TEMP"
gh_set_var GEMINI_REASONING_EFFORT "$EFFORT"
gh_set_var GEMINI_CHUNK_MAX_CHARS "$CHUNK"

echo ""
if [ "$DRY_RUN" = 1 ]; then
  echo "DRY-RUN: ничего не записано. Уберите --dry-run для реальной записи."
else
  echo "Готово. Дальше: Actions → «ChatSum scheduled summaries» → Run workflow."
fi
