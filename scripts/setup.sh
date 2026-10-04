#!/usr/bin/env bash
# Bootstrap окружения ChatSum: venv + зависимости + заготовка private.txt.
# Идемпотентен: безопасно запускать повторно (чинит битое venv).
# Дальше: заполните private.txt и смотрите README «Форк за 10 минут».
set -euo pipefail
cd "$(dirname "$0")/.."   # корень репо (сам скрипт живёт в scripts/)

VENV_DIR="venv"

echo "== 1/4 Python =="
if ! command -v python3 >/dev/null; then
  echo "❌ python3 не найден. Ubuntu/Debian: sudo apt install -y python3 python3-venv python3-pip"
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "❌ Нужен Python 3.10+, найден: $(python3 --version)"
  exit 1
fi
if ! python3 -c 'import venv, ensurepip' 2>/dev/null; then
  echo "❌ Нет модуля venv. Ubuntu/Debian: sudo apt install -y python3-venv"
  exit 1
fi
echo "✅ $(python3 --version)"

echo "== 2/4 Виртуальное окружение ($VENV_DIR) =="
NEED_VENV=0
if [ ! -x "$VENV_DIR/bin/python" ]; then
  NEED_VENV=1
elif ! "$VENV_DIR/bin/python" -c 'import telethon' 2>/dev/null; then
  echo "⚠️  $VENV_DIR существует, но зависимости битые — пересоздаю."
  NEED_VENV=1
fi
if [ "$NEED_VENV" = 1 ]; then
  rm -rf "$VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi
echo "✅ venv готов"

echo "== 3/4 Зависимости =="
"$VENV_DIR/bin/pip" install -q -r requirements.txt
"$VENV_DIR/bin/python" -c 'import telethon, dotenv; print("✅ зависимости на месте")'

echo "== 4/4 Конфигурация и gh =="
if [ ! -f private.txt ]; then
  cp private.txt.example private.txt
  echo "✅ создан private.txt из примера — ОТРЕДАКТИРУЙТЕ его (ключи)"
else
  echo "✅ private.txt уже есть (не трогаю)"
fi
if command -v gh >/dev/null; then
  echo "✅ gh найден: $(gh --version | head -1)"
else
  echo "⚠️  gh не найден — нужен только для GitHub Actions (шаг push_github_secrets.sh)."
  echo "   Установка: https://cli.github.com/ , затем: gh auth login"
fi

echo ""
echo "Готово. Дальше:"
echo "  1. Заполните private.txt (Telegram API + Google ключи)"
echo "  2. VPS-профиль:  ./$VENV_DIR/bin/python main.py"
echo "  3. Actions-сессия: ./$VENV_DIR/bin/python tools/gen_session.py   (попросит код из Telegram)"
echo "  4. Секреты в GitHub: ./scripts/push_github_secrets.sh"
