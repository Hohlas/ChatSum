#!/usr/bin/env python3
"""Однократный запуск срочных заданий — точка входа для GitHub Actions (вариант B).

Запускается по cron каждого 5 минут, определяет, какие задания из SCHEDULE.txt
попали в окно [now - LAG_MAX, now] (время МСК), выполняет их через общее ядро
main.scheduled_analysis_job и дедуплицирует через state.json (ветка `state`).

Режимы:
  python run_once.py --due                              # всё, что в окне
  python run_once.py --chat-id -100... --period 1d [--post-source] [--post-tg]
  python run_once.py --list                             # напечатать расписание
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

# private.txt используем только на VPS; на Actions env приходит из секретов.
# override=False: уже заданные env (секреты Actions) не затираются.
load_dotenv('private.txt', override=False)

MSK = timezone(timedelta(hours=3))
STATE_DEFAULT = 'state.json'
LAG_MAX_DEFAULT = 15    # минуты
STATE_RETENTION_DAYS = 3


def parse_args(argv):
    p = argparse.ArgumentParser(description='Выполнить одну срочную задачу саммари и выйти.')
    g = p.add_mutually_exclusive_group()
    g.add_argument('--due', action='store_true',
                   help='Выполнить все задания из SCHEDULE.txt, попавшие в текущее окно')
    g.add_argument('--list', action='store_true', help='Напечатать расписание и выйти')
    g.add_argument('--chat-id', type=int, help='Chat ID для ручного прогона')
    p.add_argument('--period', default='1d', help='Период анализа (1d, 12h)')
    p.add_argument('--post-source', action='store_true', help='Публиковать результат в исходном чате')
    p.add_argument('--post-tg', action='store_true', help='Отправлять как сообщение Telegram вместо Telegraph')
    p.add_argument('--lag-max', type=int, default=LAG_MAX_DEFAULT,
                   help=f'Допуск опозданий окна, минут (по умолчанию {LAG_MAX_DEFAULT})')
    args = p.parse_args(argv)
    if not (args.due or args.list or args.chat_id is not None):
        p.error('Укажите --due, --list или --chat-id')
    return args


# ──────────────────────────────────────────────
# Состояние (делдупликация)
# ──────────────────────────────────────────────

def state_path():
    return os.getenv('STATE_PATH', STATE_DEFAULT)


def load_state(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault('completed', {})
            return data
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"⚠️ Не удалось прочитать state {path}: {e}")
    return {'last_run_utc': None, 'completed': {}}


def save_state(path, state):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def prune_state(state):
    """Оставляет в completed только записи за последние ~3 суток (МСК)."""
    completed = state.setdefault('completed', {})
    if not completed:
        return
    cutoff = (datetime.now(MSK).date() - timedelta(days=STATE_RETENTION_DAYS)).isoformat()
    state['completed'] = {k: v for k, v in completed.items() if v >= cutoff}


def task_key(entry):
    """Ключ dedup-таска: chat_id|HH:MM|period[suffixes]."""
    period = entry['period']
    if entry.get('post_to_source'):
        period += '+'
    if entry.get('post_as_telegram'):
        period += '-'
    return f"{entry['chat_id']}|{entry['hour']:02d}:{entry['minute']:02d}|{period}"


# ──────────────────────────────────────────────
# Окно и должные задания (чистая логика, тестируемая без Telegram)
# ──────────────────────────────────────────────

def compute_due(entries, now_msk_naive, lag_max):
    """Возвращает [(entry, occurrence_naive_msk, date_key)] для заданий в окне.

    now_msk_naive — текущее время МСК (naive, без tz). Границы округлены к минуте.
    occurrence — ближайшее прошедшее наступление HH:MM в МСК (в пределах суток).
    date_key — МСК-дата occurrence (используется как значение в state.completed).
    """
    now = now_msk_naive.replace(second=0, microsecond=0)
    window_start = now - timedelta(minutes=lag_max)
    due = []
    for entry in entries:
        slot = now.replace(hour=entry['hour'], minute=entry['minute'])
        occurrence = slot if slot <= now else slot - timedelta(days=1)
        if window_start <= occurrence <= now:
            due.append((entry, occurrence, occurrence.strftime('%Y-%m-%d')))
    return due


# ──────────────────────────────────────────────
# Preflight окружения (понятные ошибки до start())
# ──────────────────────────────────────────────

def preflight_import_env():
    """Минимум для безопасного импорта main (на верхнем уровне читается int(API_ID)).

    Проверяем до import main: int(API_ID) и конструктивность StringSession,
    иначе битый/пустой TELEGRAM_SESSION уронит import (ValueError).
    """
    api_id = os.getenv('TELEGRAM_API_ID', '').strip()
    if not api_id:
        print("❌ TELEGRAM_API_ID не задан (GitHub Secret TELEGRAM_API_ID).")
        return False
    try:
        int(api_id)
    except ValueError:
        print(f"❌ TELEGRAM_API_ID должен быть числом, получено: {api_id!r}")
        return False

    # В Actions env TELEGRAM_SESSION всегда определён (часто пустой, если секрет
    # не заполнен). Пустой секрет — это ОШИБКА конфигурации: со строкой-пустышкой
    # main.py трактует маршрут как VPS-файловую сессию. Отличаем «пуст» от «не задан».
    if 'TELEGRAM_SESSION' in os.environ and not os.environ['TELEGRAM_SESSION'].strip():
        print("❌ TELEGRAM_SESSION пуст (секрет TELEGRAM_SESSION не заполнен).")
        print("   Перевыпустите сессию: python gen_session.py, обновите секрет.")
        return False

    session_str = os.getenv('TELEGRAM_SESSION', '').strip()
    if session_str:
        from telethon.sessions import StringSession
        try:
            StringSession(session_str)
        except Exception:
            print("❌ TELEGRAM_SESSION повреждён/некорректен (StringSession не создаётся).")
            print("   Перевыпустите сессию: python gen_session.py, обновите секрет.")
            return False
    return True


def preflight_actions_env():
    """Полный набор env для выполнения задач — до client.start()."""
    ok = True
    for var, label in [
        ('TELEGRAM_API_HASH', 'TELEGRAM_API_HASH'),
        ('TELEGRAM_PHONE', 'TELEGRAM_PHONE'),
        ('TELEGRAM_SESSION', 'TELEGRAM_SESSION (StringSession)'),
        ('GOOGLE_API_KEY', 'GOOGLE_API_KEY'),
    ]:
        if not os.getenv(var, '').strip():
            print(f"❌ Не задан {label}. Проверьте Secrets в настройках репозитория.")
            ok = False
    return ok


# ──────────────────────────────────────────────
# Команды
# ──────────────────────────────────────────────

def cmd_list(main):
    entries = main.load_schedule(main.SCHEDULE_FILE)
    if not entries:
        print("(расписание пусто)")
        return 0
    print("Расписание (МСК):")
    for e in entries:
        period = e['period'] + ('+' if e['post_to_source'] else '') + ('-' if e.get('post_as_telegram') else '')
        sched_key = task_key(e)
        print(f"  {e['chat_id']} | {e['hour']:02d}:{e['minute']:02d} | {period}   (key: {sched_key})")
    return 0


async def run_due(main, args):
    now_msk = datetime.now(MSK)
    now_naive = now_msk.replace(second=0, microsecond=0)
    entries = main.load_schedule(main.SCHEDULE_FILE)
    due = compute_due(entries, now_naive, args.lag_max)
    if not due:
        print(f"[{now_msk.strftime('%Y-%m-%d %H:%M:%S')} МСК] Нет заданий в окне (lag_max={args.lag_max} мин).")
        return 0

    path = state_path()
    state = load_state(path)
    completed = state['completed']

    pending = []
    for entry, occurrence, date_key in due:
        key = task_key(entry)
        if completed.get(key) == date_key:
            print(f"⏭️  Уже выполнено: {key} ({date_key})")
            continue
        pending.append((entry, occurrence, key, date_key))

    if not pending:
        print("Все должные задания уже выполнены (dedup).")
        return 0

    try:
        await main.telegram_client.start(phone=main.PHONE)
    except Exception as e:
        print(f"❌ Не удалось подключиться к Telegram: {e}")
        print("   Проверьте TELEGRAM_SESSION / TELEGRAM_API_ID / TELEGRAM_API_HASH.")
        return 1

    failed = 0
    try:
        for entry, occurrence, key, date_key in pending:
            print(f"▶️  Выполняю: {key} (слот {occurrence} МСК)")
            try:
                ok = await main.scheduled_analysis_job(
                    entry['chat_id'],
                    entry['period'],
                    entry['post_to_source'],
                    entry.get('post_as_telegram', False),
                )
            except Exception as e:
                ok = False
                print(f"❌ Исключение при выполнении {key}: {e}")
            if ok is True:
                completed[key] = date_key
                state['last_run_utc'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                prune_state(state)
                save_state(path, state)
                print(f"✅ {key} выполнено, записано в state")
            else:
                failed += 1
                print(f"❌ {key} НЕ выполнено — в state не записываю (будет повторено)")
    finally:
        try:
            await main.telegram_client.disconnect()
        except Exception as e:
            print(f"⚠️ Ошибка при disconnect: {e}")
        try:
            await main.http_client.aclose()
        except Exception as e:
            print(f"⚠️ Ошибка при закрытии http_client: {e}")

    return 1 if failed else 0


async def run_manual(main, args):
    clean, plus, minus = main._parse_suffixes(args.period)
    post_to_source = args.post_source or plus
    post_as_telegram = args.post_tg or minus
    if not re.fullmatch(r'\d+[hd]', clean):
        print(f"❌ Некорректный период: {args.period!r}. Ожидалось, например, 1d или 12h.")
        return 2
    print(f"▶️  Ручной прогон: chat {args.chat_id}, период {clean}"
          + (" (+в исходный чат)" if post_to_source else "")
          + (" (как TG-сообщение)" if post_as_telegram else ""))
    try:
        await main.telegram_client.start(phone=main.PHONE)
    except Exception as e:
        print(f"❌ Не удалось подключиться к Telegram: {e}")
        print("   Проверьте TELEGRAM_SESSION / TELEGRAM_API_ID / TELEGRAM_API_HASH.")
        return 1
    ok = False
    try:
        ok = await main.scheduled_analysis_job(args.chat_id, clean, post_to_source, post_as_telegram)
    except Exception as e:
        print(f"❌ Исключение при выполнении: {e}")
    finally:
        try:
            await main.telegram_client.disconnect()
        except Exception as e:
            print(f"⚠️ Ошибка при disconnect: {e}")
        try:
            await main.http_client.aclose()
        except Exception as e:
            print(f"⚠️ Ошибка при закрытии http_client: {e}")
    return 0 if ok is True else 1


def run(argv):
    args = parse_args(argv)

    if not preflight_import_env():
        return 2

    # main.py на верхнем уровне создаёт TelegramClient, которому нужен
    # текущий event loop (Telethon читает asyncio.get_event_loop()).
    # На Python 3.10+ цикл в main-потоке не создаётся автоматически —
    # создаём явно до импорта main.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

    import main  # noqa: F401  (импорт безопасен после preflight)

    if args.list:
        return cmd_list(main)

    if not preflight_actions_env():
        return 2

    try:
        main.load_env_config()
    except SystemExit:
        print("Ошибка конфигурации (см. выше). Проверьте Secrets/Variables репозитория.")
        return 2

    if args.due:
        return asyncio.run(run_due(main, args))
    return asyncio.run(run_manual(main, args))


if __name__ == '__main__':
    sys.exit(run(sys.argv[1:]))