#!/usr/bin/env python3
"""Однократный запуск срочных заданий — точка входа для GitHub Actions (вариант B).

Запускается по cron каждые 10 минут, определяет, какие задания из SCHEDULE.txt
попали в окно [now - LAG_MAX, now] (время МСК), выполняет их через общее ядро
main.scheduled_analysis_job и дедуплицирует через state.json (ветка `state`).
В режиме --watch дополнительно опрашивает inbox (тема General канала
результатов): команда со ссылкой (`sum20 t.me/…`) либо команда в топике чата →
выполнение через main.run_analysis.

Режимы:
  python run_once.py --due                              # всё, что в окне
  python run_once.py --watch [--watch-seconds 480] [--poll-interval 60]
  python run_once.py --chat-id -100... --period 1d [--post-source] [--post-tg]
  python run_once.py --list                             # напечатать расписание
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

# private.txt используем только на VPS; на Actions env приходит из секретов.
# override=False: уже заданные env (секреты Actions) не затираются.
load_dotenv('private.txt', override=False)

MSK = timezone(timedelta(hours=3))
STATE_DEFAULT = 'state.json'
LAG_MAX_DEFAULT = 45    # минуты: cron GitHub нередко стартует/опазывает на 30-45 мин,
                        # окно должно перекрывать «gap» между соседними ранами, иначе
                        # слот навсегда выпадает (следующий ран видит его уже вне окна)
STATE_RETENTION_DAYS = 3


def parse_args(argv):
    p = argparse.ArgumentParser(description='Выполнить одну срочную задачу саммари и выйти.')
    g = p.add_mutually_exclusive_group()
    g.add_argument('--due', action='store_true',
                   help='Выполнить все задания из SCHEDULE.txt, попавшие в текущее окно')
    g.add_argument('--watch', action='store_true',
                   help='Цикл ~watch-seconds: due-задачи + опрос inbox (форвард + sum/copy)')
    g.add_argument('--list', action='store_true', help='Напечатать расписание и выйти')
    g.add_argument('--chat-id', type=int, help='Chat ID для ручного прогона')
    p.add_argument('--watch-seconds', type=int, default=480,
                   help='Длительность цикла --watch, секунд (по умолчанию 480)')
    p.add_argument('--poll-interval', type=int, default=60,
                   help='Пауза между итерациями --watch, секунд (по умолчанию 60)')
    p.add_argument('--period', default='1d', help='Период анализа (1d, 12h)')
    p.add_argument('--post-source', action='store_true', help='Публиковать результат в исходном чате')
    p.add_argument('--post-tg', action='store_true', help='Отправлять как сообщение Telegram вместо Telegraph')
    p.add_argument('--lag-max', type=int, default=LAG_MAX_DEFAULT,
                   help=f'Допуск опозданий окна, минут (по умолчанию {LAG_MAX_DEFAULT})')
    args = p.parse_args(argv)
    if not (args.due or args.list or args.watch or args.chat_id is not None):
        p.error('Укажите --due, --watch, --list или --chat-id')
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


KEY_CURSOR_STATE_FIELD = 'google_key_cursor'


def restore_key_cursor(main, state):
    """Продолжить ротацию Google-ключей с места прошлого рана.

    Без этого каждый ран стартует с ключа 1 (процесс эфемерный). Не роняет:
    main без курсора (тестовые фейки) — молча пропускаем.
    """
    cursor = 0
    try:
        cursor = int((state or {}).get(KEY_CURSOR_STATE_FIELD, 0) or 0)
    except (TypeError, ValueError):
        cursor = 0
    if cursor < 0:
        cursor = 0
    setter = getattr(main, 'set_google_key_cursor', None)
    if callable(setter):
        try:
            setter(cursor)
        except Exception:
            pass
    n = len(getattr(main, 'GOOGLE_API_KEYS', []) or [])
    if n:
        print(f"🔑 Курсор ключей из state: {cursor} → старт с ключа {cursor % n + 1}/{n}")
    return cursor


def store_key_cursor(main, state):
    """Записать текущий счётчик ключей в state (продолжение ротации в след. ране)."""
    getter = getattr(main, 'get_google_key_cursor', None)
    if not callable(getter):
        return state
    try:
        state[KEY_CURSOR_STATE_FIELD] = int(getter())
    except Exception:
        pass
    return state


def git_push_schedule(message):
    """Коммитит SCHEDULE.txt и пушит в origin main (rebase-retry при гонке).

    Возвращает True/False. Вызывается только когда расписание уже сохранено
    в файл; при False файл локально изменён, но в GitHub ничего не ушло.
    """
    import subprocess

    def run(args):
        return subprocess.run(['git', *args], capture_output=True, text=True)

    if run(['add', '--', 'SCHEDULE.txt']).returncode != 0:
        print("⚠️ git add SCHEDULE.txt не прошёл")
        return False
    r = run(['-c', 'user.name=chatsum-bot',
             '-c', 'user.email=chatsum-bot@users.noreply.github.com',
             'commit', '-m', message])
    if r.returncode != 0:
        out = (r.stdout or '') + (r.stderr or '')
        if 'nothing to commit' in out:
            return True  # содержимое уже закоммичено
        print(f"⚠️ git commit не прошёл: {out.strip()}")
        return False
    for attempt in range(1, 4):
        # HEAD:main — работает и на detached HEAD (так чекаутит checkout@v4)
        r = run(['push', 'origin', 'HEAD:main'])
        if r.returncode == 0:
            return True
        print(f"⚠️ git push отклонён (попытка {attempt}/3): "
              f"{(r.stderr or r.stdout).strip()}")
        # Гонка с чужим пушем: подтянуть историю (shallow → unshallow) и rebase.
        run(['fetch', '--unshallow', 'origin'])  # не shallow → ошибка, неважно
        f = run(['fetch', 'origin', 'main'])
        if f.returncode != 0:
            print(f"⚠️ git fetch не прошёл: {(f.stderr or f.stdout).strip()}")
            return False
        p = run(['rebase', 'origin/main'])
        if p.returncode != 0:
            print(f"⚠️ rebase не удался: {(p.stderr or p.stdout).strip()}")
            run(['rebase', '--abort'])
            return False
    return False


# ──────────────────────────────────────────────
# Окно и должные задания (чистая логика, тестируемая без Telegram)
# ──────────────────────────────────────────────

def compute_due(entries, now_msk_naive, lag_max, since_msk_naive=None):
    """Возвращает [(entry, occurrence_naive_msk, date_key)] для заданий в окне.

    now_msk_naive — текущее время МСК (naive, без tz). Границы округлены к минуте.
    occurrence — ближайшее прошедшее наступление HH:MM в МСК (в пределах суток).
    date_key — МСК-дата occurrence (используется как значение в state.completed).

    since_msk_naive — время последнего рана (МСК). Если задано, окно расширяется
    до него: слот, который выпал из окна из-за простоя раннера (GitHub-очередь),
    догоняется следующим раном. Пересечение полуночи безопасно: попадает лишь
    последнее occurrence на каждый слот.
    """
    now = now_msk_naive.replace(second=0, microsecond=0)
    window_start = now - timedelta(minutes=lag_max)
    if since_msk_naive is not None:
        window_start = min(window_start, since_msk_naive.replace(second=0, microsecond=0))
    due = []
    for entry in entries:
        slot = now.replace(hour=entry['hour'], minute=entry['minute'])
        occurrence = slot if slot <= now else slot - timedelta(days=1)
        if window_start <= occurrence <= now:
            due.append((entry, occurrence, occurrence.strftime('%Y-%m-%d')))
    return due


def _last_run_msk_naive(state):
    """Время последнего успешного рана из state как naive-МСК или None.

    Используется для догона слотов, выпавших из окна лага при простое раннера.
    """
    raw = (state or {}).get('last_run_utc')
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK).replace(tzinfo=None)


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
    ]:
        if not os.getenv(var, '').strip():
            print(f"❌ Не задан {label}. Проверьте Secrets в настройках репозитория.")
            ok = False
    if not os.getenv('GOOGLE_API_KEY', '').strip() and not os.getenv('GOOGLE_API_KEYS', '').strip():
        print("❌ Не задан GOOGLE_API_KEY (или сводный GOOGLE_API_KEYS). Проверьте Secrets в настройках репозитория.")
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


async def run_due_once(main, args, state, path):
    """Одна итерация due-задач: compute → dedup → выполнение → save.

    Соединение уже установлено вызывателем (run_due / run_watch).
    Возвращает число невыполненных задач (0 = всё хорошо).
    """
    now_msk = datetime.now(MSK)
    now_naive = now_msk.replace(second=0, microsecond=0)
    since_naive = _last_run_msk_naive(state)
    entries = main.load_schedule(main.SCHEDULE_FILE)
    due = compute_due(entries, now_naive, args.lag_max, since_naive)
    if not due:
        print(f"[{now_msk.strftime('%Y-%m-%d %H:%M:%S')} МСК] Нет заданий в окне (lag_max={args.lag_max} мин).")
        return 0

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

    failed = 0
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
    return failed


async def run_due(main, args):
    path = state_path()
    state = load_state(path)
    restore_key_cursor(main, state)

    try:
        await main.telegram_client.start(phone=main.PHONE)
    except Exception as e:
        print(f"❌ Не удалось подключиться к Telegram: {e}")
        print("   Проверьте TELEGRAM_SESSION / TELEGRAM_API_ID / TELEGRAM_API_HASH.")
        return 1

    try:
        failed = await run_due_once(main, args, state, path)
    finally:
        store_key_cursor(main, state)
        try:
            save_state(path, state)
        except Exception as e:
            print(f"⚠️ Не удалось сохранить state: {e}")
        try:
            await main.telegram_client.disconnect()
        except Exception as e:
            print(f"⚠️ Ошибка при disconnect: {e}")
        try:
            await main.http_client.aclose()
        except Exception as e:
            print(f"⚠️ Ошибка при закрытии http_client: {e}")

    return 1 if failed else 0


# ──────────────────────────────────────────────
# Inbox: форвард + команда sum/copy в General канала результатов
# ──────────────────────────────────────────────

INBOX_INITIAL_LIMIT = 100  # глубина первого опроса (покрывает межрановый зазор)

# Ссылка на чат в тексте команды: t.me/chatname | @chatname | t.me/c/ID[/msgid].
LINK_RE = re.compile(
    r'(?:https?://)?t\.me/(c/(\d+)(?:/\d+)?|[A-Za-z0-9_]{5,})', re.IGNORECASE)
AT_RE = re.compile(r'(?<![\w@/])@([A-Za-z0-9_]{5,})')
# Служебные t.me-пути — не чаты (joinchat требует вступления, такое не умеем).
LINK_RESERVED = {'joinchat', 'iv', 'share', 'socks', 'proxy', 'addstickers',
                 'addemoji', 'boost', 'setlanguage', 'c'}

# Команда расписания: [sch HH:MM] <команда|период>. Время обязательно.
#   22:33 sum1d t.me/Чат   22:33 sum20      /sch 09:00 1d      22:33 1d+
# sum/copy — c digit-guard как в parse_chat_command_args ('summary' не команда).
SCHED_RE = re.compile(
    r'^(?:/?sch\s+)?(\d{1,2}):(\d{2})\s+'
    r'(/?(?:sum|copy)(?=[\d+\-]|\s).*|\d+[hd][+-]*|\d+[+-]*)$', re.IGNORECASE)


def extract_chat_link(text):
    """Ссылка на чат из текста команды или None.

    Возвращает ('username', name) | ('internal', -100ID).
    Голые слова ссылками НЕ считаются (только t.me/… или @…), иначе опечатка
    будет вечно ретраиться как резолв.
    """
    m = LINK_RE.search(text or '')
    if m:
        if m.group(2) is not None:
            return ('internal', int(f"-100{m.group(2)}"))
        name = m.group(1)
        if name.lower() not in LINK_RESERVED:
            return ('username', name)
        return None
    m = AT_RE.search(text or '')
    if m:
        return ('username', m.group(1))
    return None


async def fetch_forum_topics(main, dest):
    """{topic_id: title} канала результатов (кэш за ран берёт вызыватель)."""
    try:
        from telethon.tl.functions.channels import GetForumTopicsRequest
        result = await main.telegram_client(GetForumTopicsRequest(
            channel=dest, offset_date=0, offset_id=0, offset_topic=0, limit=100))
        return {t.id: t.title for t in getattr(result, 'topics', [])
                if getattr(t, 'id', None) and getattr(t, 'title', None)}
    except Exception as e:
        print(f"⚠️ Inbox: не удалось получить список топиков: {e}")
        return {}


async def fetch_dialog_names(main):
    """{точное название: chat_id} по всем диалогам + список дублей названий."""
    from telethon.utils import get_peer_id
    names = {}
    dups = set()
    try:
        async for d in main.telegram_client.iter_dialogs():
            ent = getattr(d, 'entity', None)
            if ent is None:
                continue
            title = getattr(ent, 'title', None) or None
            if not title:
                first = getattr(ent, 'first_name', None)
                if first:
                    title = first + (f" {ent.last_name}" if getattr(ent, 'last_name', None) else "")
            if not title:
                continue
            try:
                cid = get_peer_id(ent)
            except Exception:
                continue
            if title in names and names[title] != cid:
                dups.add(title)
            else:
                names[title] = cid
    except Exception as e:
        print(f"⚠️ Inbox: не удалось получить диалоги: {e}")
    return names, dups


async def resolve_topic_source(main, mem, dest, top_id):
    """Источник команды, написанной в топике: название топика = название чата.

    Возвращает chat_id или None. Кэширует топики и диалоги за ран в mem.
    Неопознанное логирует вызыватель (один раз за ран).
    """
    if dest == 'me':
        return None
    if mem.get('topics') is None:
        mem['topics'] = await fetch_forum_topics(main, dest)
    title = mem['topics'].get(top_id)
    if not title:
        return None
    if mem.get('dialogs') is None:
        names, dups = await fetch_dialog_names(main)
        mem['dialogs'] = names
        for dup in sorted(dups):
            print(f"⚠️ Inbox: название '{dup}' есть у нескольких чатов — беру первый")
    src = mem['dialogs'].get(title)
    return src


async def resolve_name_source(main, mem, text, exclude):
    """Источник команды из General: чат, упомянутый названием в тексте команды.

    Нужно, т.к. в General нет заголовка топика, а распознать чат можно и без
    ссылки. Ищем название диалога (от длинных к коротким) как подстроку текста.
    exclude — id командного чата, чтобы '@username' не сматчился на себя.
    Возвращает chat_id или None. Кэш диалогов — в mem за ран.
    """
    if mem.get('dialogs') is None:
        names, dups = await fetch_dialog_names(main)
        mem['dialogs'] = names
        for dup in sorted(dups):
            print(f"⚠️ Inbox: название '{dup}' есть у нескольких чатов — беру первый")
    low = (text or '').lower()
    for title, cid in sorted(mem['dialogs'].items(), key=lambda kv: -len(kv[0])):
        if cid == exclude:
            continue
        if len(title) >= 3 and title.lower() in low:
            return cid
    return None


def msg_topic_id(msg):
    """id топика из заголовка ответа Telethon или None (General/не топик).

    У Telethon.Message НЕТ атрибута reply_to_top_id — только заголовок
    reply_to: forum_topic + reply_to_msg_id (+ reply_to_top_id, если ответ
    не на корень). id корня топика совпадает с id топика, поэтому
    reply_to_msg_id — рабочая замена. Важно: опрос итерирует ВСЕ топики
    канала, General отдельно не выделяется — топик определяем только так.
    """
    r = getattr(msg, 'reply_to', None)
    if r is None or not getattr(r, 'forum_topic', False):
        return None
    return getattr(r, 'reply_to_top_id', None) or getattr(r, 'reply_to_msg_id', None)


def new_inbox_mem():
    """Память inbox внутри одного рана: кэши топиков/диалогов + антиспам лога."""
    return {'topics': None, 'dialogs': None, 'skip_logged': set()}


def _log_once(mem, key, text):
    """Пропуск логируем один раз за ран (не засоряем лог каждые 20 сек)."""
    if key not in mem['skip_logged']:
        mem['skip_logged'].add(key)
        print(text)


async def _notify_inbox(main, dest, topic_id, text):
    """Диагностика inbox: в топик (reply_to=top) или в General (без reply_to).

    Best-effort: неуспех отправки ран не роняет.
    """
    try:
        if topic_id and topic_id != 1:
            await main.telegram_client.send_message(dest, text, reply_to=topic_id)
        else:
            await main.telegram_client.send_message(dest, text)
    except Exception as e:
        print(f"⚠️ Inbox: не удалось отправить диагностику: {e}")


async def _drop_command(main, dest, msg, why):
    """Удаление отработанной/проваленной команды. Best-effort, ран не роняет."""
    try:
        await main.telegram_client.delete_messages(dest, [msg.id])
        print(f"🗑️ Inbox {msg.id}: команда удалена ({why})")
        return True
    except Exception as e:
        print(f"⚠️ Inbox {msg.id}: не удалось удалить команду ({why}): {e}")
        return False


async def _sched_list(main, mem, dest, msg):
    """'sch' → публикация полного расписания. Команда удаляется."""
    top_id = msg_topic_id(msg)
    home = top_id if top_id and top_id != 1 else None
    text = await main.schedule_listing_text()
    await _drop_command(main, dest, msg, 'выполнено')
    await _notify_inbox(main, dest, home, text)
    print(f"📋 Inbox {msg.id}: sch — расписание опубликовано")
    return 'ok'


async def _sched_unsch(main, mem, dest, msg, text):
    """'unsch [ссылка]' → убрать чат из расписания (источник: ссылка/топик)."""
    from telethon.utils import get_peer_id
    snippet = text if len(text) <= 60 else text[:57] + '...'
    top_id = msg_topic_id(msg)
    home = top_id if top_id and top_id != 1 else None

    async def fail(log_text, notice_text):
        print(f"{log_text} | {snippet!r}")
        if await _drop_command(main, dest, msg, 'провал'):
            await _notify_inbox(main, dest, home,
                                f"{notice_text}\nКоманда: {snippet!r}")
        return 'fail'

    source_id = None
    link = extract_chat_link(text)
    if link is not None:
        kind, value = link
        if kind == 'internal':
            source_id = value
        else:
            try:
                entity = await main.telegram_client.get_entity(value)
                source_id = get_peer_id(entity)
            except Exception as e:
                return await fail(
                    f"❌ Inbox {msg.id}: unsch — не резолвится {value!r}: {e}",
                    f"❌ Inbox {msg.id}: не резолвится ссылка — команда удалена.")
    if source_id is None:
        if home and home != 1:
            source_id = await resolve_topic_source(main, mem, dest, home)
        if source_id is None:
            return await fail(
                f"❌ Inbox {msg.id}: unsch без ссылки и вне топика",
                f"❌ Inbox {msg.id}: укажите чат — ссылку t.me/... или "
                f"напишите unsch в топике чата. Удалена, ничего не снято.")

    entries = main.load_schedule(main.SCHEDULE_FILE)
    remain = [e for e in entries if e['chat_id'] != source_id]
    if len(remain) == len(entries):
        await _drop_command(main, dest, msg, 'выполнено')
        await _notify_inbox(main, dest, home,
                            f"ℹ️ Чат {source_id} не найден в расписании.")
        return 'ok'
    if not main.save_schedule(main.SCHEDULE_FILE, remain):
        return await fail(f"❌ Inbox {msg.id}: ошибка записи SCHEDULE.txt",
                          f"❌ Inbox {msg.id}: ошибка записи расписания.")
    if not git_push_schedule(f"sched: unsch {source_id}"):
        main.save_schedule(main.SCHEDULE_FILE, entries)  # откат локально
        return await fail(f"❌ Inbox {msg.id}: git push не прошёл (unsch)",
                          f"❌ Inbox {msg.id}: не удалось запушить в GitHub — "
                          f"расписание не изменено.")
    await _drop_command(main, dest, msg, 'выполнено')
    listing = await main.schedule_listing_text()
    await _notify_inbox(main, dest, home,
                        f"✅ Чат {source_id} убран из расписания.\n\n{listing}")
    print(f"🗑️ Inbox {msg.id}: unsch {source_id} — снято и запушено")
    return 'ok'


async def _sched_add(main, mem, dest, msg, m):
    """'HH:MM sum1d [ссылка]' → запись в SCHEDULE.txt + git push.

    Период: из sum-команды (schedule_period_from_parsed) либо готовый
    ('1d', '20', '1d+'). Чат: ссылка → топик (название = чат).
    Занятый слот → сдвиг +5 мин. Успех → удаление + подтверждение + список.
    """
    from telethon.utils import get_peer_id
    hour, minute, cmd = int(m.group(1)), int(m.group(2)), m.group(3).strip()
    snippet = m.group(0) if len(m.group(0)) <= 60 else m.group(0)[:57] + '...'
    top_id = msg_topic_id(msg)
    home = top_id if top_id and top_id != 1 else None

    async def fail(log_text, notice_text):
        print(f"{log_text} | {snippet!r}")
        if await _drop_command(main, dest, msg, 'провал'):
            await _notify_inbox(main, dest, home,
                                f"{notice_text}\nКоманда: {snippet!r}")
        return 'fail'

    if hour > 23 or minute > 59:
        return await fail(f"❌ Inbox {msg.id}: неверное время {hour:02d}:{minute:02d}",
                          f"❌ Inbox {msg.id}: неверное время — нужно 00:00–23:59. "
                          f"Команда удалена.")

    # Период: sum/copy-команда → производный; иначе готовый '1d'/'20'/'1d+'
    if re.match(r'^/?(?:sum|copy)(?=[\d+\-]|\s)', cmd, re.IGNORECASE):
        try:
            parsed = main.parse_chat_command_args(cmd)
        except Exception as e:
            return await fail(f"❌ Inbox {msg.id}: не распарсить {cmd!r}: {e}",
                              f"❌ Inbox {msg.id}: команда не распознана — удалена.")
        if parsed is None:
            return await fail(f"❌ Inbox {msg.id}: не команда {cmd!r}",
                              f"❌ Inbox {msg.id}: команда не распознана — удалена.")
        if not parsed.get('use_ai'):
            return await fail(f"❌ Inbox {msg.id}: в расписании только sum (copy)",
                              f"❌ Inbox {msg.id}: в расписании поддерживается "
                              f"только sum (не copy). Команда удалена.")
        period = main.schedule_period_from_parsed(parsed)
        if period is None:
            return await fail(f"❌ Inbox {msg.id}: период не сводится к SCHEDULE",
                              f"❌ Inbox {msg.id}: в расписание ставятся только "
                              f"одиночные периоды (sum1d, sum12h, sum20) — "
                              f"диапазоны/time_range не поддерживаются. Удалена.")
    else:
        period = cmd.lower()  # готовый формат SCHEDULE ('1d', '20', '1d+')
        clean, _, _ = main._parse_suffixes(period)
        if not re.fullmatch(r'\d+[hd]|\d+', clean):
            return await fail(f"❌ Inbox {msg.id}: битый период {cmd!r}",
                              f"❌ Inbox {msg.id}: период {cmd!r} не понятен "
                              f"(ожидалось 1d/12h/20). Команда удалена.")

    # Источник: ссылка → топик
    source_id = None
    link = extract_chat_link(cmd)
    if link is not None:
        kind, value = link
        if kind == 'internal':
            source_id = value
        else:
            try:
                entity = await main.telegram_client.get_entity(value)
                source_id = get_peer_id(entity)
            except Exception as e:
                return await fail(
                    f"❌ Inbox {msg.id}: не резолвится ссылка {value!r}: {e}",
                    f"❌ Inbox {msg.id}: не резолвится ссылка {value!r} — "
                    f"команда удалена, повтора не будет.")
    if source_id is None:
        if home and home != 1:
            source_id = await resolve_topic_source(main, mem, dest, home)
        if source_id is None:
            source_id = await resolve_name_source(main, mem, cmd, exclude=dest)
        if source_id is None:
            return await fail(
                f"❌ Inbox {msg.id}: расписание без ссылки и вне топика",
                f"❌ Inbox {msg.id}: не нашёл чат — нужна ссылка t.me/.../@... "
                f"или точное название чата, либо команда в топике чата. "
                f"Удалена, ничего не добавлено.")

    # Дубль (точное совпадение чат+время+период) — не плодим
    clean, plus, minus = main._parse_suffixes(period)
    entry_args = dict(chat_id=source_id, hour=hour, minute=minute, period=clean,
                      post_to_source=plus, post_as_telegram=minus)
    entries = main.load_schedule(main.SCHEDULE_FILE)
    for e in entries:
        if (e['chat_id'] == source_id and e['hour'] == hour
                and e['minute'] == minute and e['period'] == clean
                and bool(e.get('post_to_source')) == plus
                and bool(e.get('post_as_telegram')) == minus):
            await _drop_command(main, dest, msg, 'выполнено')
            await _notify_inbox(main, dest, home,
                                f"ℹ️ Такая запись уже есть: {source_id} "
                                f"{hour:02d}:{minute:02d}, {period}")
            return 'ok'

    slot = main.schedule_free_slot(entries, hour, minute)
    if slot is None:
        return await fail(f"❌ Inbox {msg.id}: нет свободного слота за сутки",
                          f"❌ Inbox {msg.id}: все 5-мин слоты заняты — "
                          f"не добавлено. Команда удалена.")
    h, mi = slot
    entries.append(dict(entry_args, hour=h, minute=mi))
    if not main.save_schedule(main.SCHEDULE_FILE, entries):
        return await fail(f"❌ Inbox {msg.id}: ошибка записи SCHEDULE.txt",
                          f"❌ Inbox {msg.id}: ошибка записи расписания.")
    if not git_push_schedule(f"sched: {source_id} {h:02d}:{mi:02d} {period}"):
        main.save_schedule(main.SCHEDULE_FILE, entries[:-1])  # откат локально
        return await fail(f"❌ Inbox {msg.id}: git push не прошёл",
                          f"❌ Inbox {msg.id}: не удалось запушить в GitHub — "
                          f"расписание локально откатлено, повторите позже.")

    await _drop_command(main, dest, msg, 'выполнено')
    shifted = (h, mi) != (hour, minute)
    time_note = f"{hour:02d}:{minute:02d}→{h:02d}:{mi:02d} (слот был занят)" \
        if shifted else f"{h:02d}:{mi:02d}"
    listing = await main.schedule_listing_text()
    await _notify_inbox(
        main, dest, home,
        f"✅ Расписание добавлено: {source_id}, {time_note}, {period}\n\n{listing}")
    print(f"📅 Inbox {msg.id}: sched +{source_id} {h:02d}:{mi:02d} {period} — запушено")
    return 'ok'


async def process_inbox_message(main, mem, dest, msg):
    """Обработка одного inbox-сообщения. Возвращает 'ok' | 'fail' | 'skip'.

    Источник команды (строго по порядку): явная ссылка → топик (только если
    msg_topic_id указывает на известный топик канала результатов; заголовок
    есть у сообщений ВСЕХ топиков — опрос их не разделяет). Всё остальное —
    General: без ссылки = провал. Успех и провал — удаление команды;
    провал — плюс диагностика с текстом команды (куда: топик источника,
    если он определён, иначе туда, где лежала команда).
    Не-команды не трогаем никогда.
    """
    from telethon.utils import get_peer_id

    text = (getattr(msg, 'text', None) or '').strip()
    if not text:
        return 'skip'

    # Команды расписания: 'sch' | 'unsch [link]' | 'HH:MM sum1d [link]' | '/sch HH:MM 1d'
    if text.lower() in ('sch', '/sch'):
        return await _sched_list(main, mem, dest, msg)
    if re.match(r'^/?unsch(\s|$)', text, re.IGNORECASE):
        return await _sched_unsch(main, mem, dest, msg, text)
    m_sc = SCHED_RE.match(text)
    if m_sc:
        return await _sched_add(main, mem, dest, msg, m_sc)

    try:
        parsed = main.parse_chat_command_args(text)
    except Exception as e:
        # Битый параметр ('sum abh'): не команда и не провал анализа —
        # пропускаем, не удаляем, опрос не отравляем.
        print(f"⚠️  Inbox {msg.id}: не удалось распарсить {text!r}: {e} — пропускаю")
        return 'skip'
    if parsed is None:
        return 'skip'  # не команда — не трогаем

    # Топик-команда: заголовок reply_to → id топика → он должен быть в
    # списке топиков канала (иначе это ответ ВНУТРИ General на конкретное
    # сообщение — трактуем как General, т.е. нужна ссылка).
    top_id = msg_topic_id(msg)
    is_topic = False
    if top_id and top_id != 1 and dest != 'me':
        if mem.get('topics') is None:
            mem['topics'] = await fetch_forum_topics(main, dest)
        is_topic = top_id in mem['topics']
    home = top_id if is_topic else None  # куда слать диагностику при неудаче

    snippet = text if len(text) <= 60 else text[:57] + '…'

    async def fail(notice_topic, log_text, notice_text):
        print(f"{log_text} | {snippet!r}")
        if await _drop_command(main, dest, msg, 'провал'):
            await _notify_inbox(main, dest, notice_topic,
                                f"{notice_text}\nКоманда: {snippet!r}")
        else:
            _log_once(mem, f"dropfail:{msg.id}",
                      f"⚠️ Inbox {msg.id}: команда не удалена — повторится следующим опросом")
        return 'fail'

    source_id = None
    via = ""
    link = extract_chat_link(text)
    if link is not None:
        kind, value = link
        if kind == 'internal':
            source_id = value
            via = " (ссылка)"
        else:
            try:
                entity = await main.telegram_client.get_entity(value)
                source_id = get_peer_id(entity)
                via = " (ссылка)"
            except Exception as e:
                return await fail(
                    home,
                    f"❌ Inbox {msg.id}: не резолвится ссылка {value!r}: {e}",
                    f"❌ Inbox {msg.id}: не резолвится ссылка {value!r} — "
                    f"команда удалена, повтора не будет.")
    if source_id is None:
        if is_topic:
            source_id = await resolve_topic_source(main, mem, dest, top_id)
            if source_id is None:
                return await fail(
                    home,
                    f"❌ Inbox {msg.id}: топик {top_id} не сопоставлен ни с одним чатом",
                    f"❌ Inbox {msg.id}: топик не сопоставлен ни с одним чатом — "
                    f"команда удалена, повтора не будет.")
            via = " (топик)"
        else:
            source_id = await resolve_name_source(main, mem, text, exclude=dest)
            if source_id is None:
                return await fail(
                    None,
                    f"❌ Inbox {msg.id}: команда без ссылки и вне топика — не выполнена",
                    f"❌ Inbox {msg.id}: не нашёл чат — укажите ссылку t.me/... "
                    f"или @username, либо точное название чата, либо напишите "
                    f"sum прямо в топике чата. Удалена, ничего не выполнено.")
            via = " (название)"

    try:
        chat_entity = await main.telegram_client.get_entity(source_id)
        if getattr(chat_entity, 'title', None):
            chat_name = chat_entity.title
        elif getattr(chat_entity, 'first_name', None):
            chat_name = chat_entity.first_name
            if getattr(chat_entity, 'last_name', None):
                chat_name += f" {chat_entity.last_name}"
        else:
            chat_name = f"чат {source_id}"
    except Exception as e:
        return await fail(
            home,
            f"❌ Inbox {msg.id}: нет доступа к чату {source_id}: {e}",
            f"❌ Inbox {msg.id}: нет доступа к чату-источнику — "
            f"команда удалена, повтора не будет.")

    use_ai = parsed['use_ai']
    print(f"▶️  Inbox {msg.id}: {'sum' if use_ai else 'copy'} из '{chat_name}' ({source_id}){via}")
    notify_topic = home
    try:
        topic_out = await main.get_or_create_topic(chat_name)
        notify_topic = topic_out
        action = "анализ" if use_ai else "экспорт"
        await main.telegram_client.send_message(
            dest, f"🔄 Начинаю {action} по команде из inbox, чат '{chat_name}'...",
            reply_to=topic_out)
        ok = await main.run_analysis(
            chat_id=source_id,
            chat_name=chat_name,
            hours=parsed['hours'],
            days=parsed['days'],
            limit=parsed['limit'],
            range_start=parsed['range_start'],
            range_end=parsed['range_end'],
            time_range_start=parsed['time_range_start'],
            time_range_end=parsed['time_range_end'],
            use_ai=use_ai,
            post_to_source=parsed['post_to_source'],
            post_as_telegram=parsed['post_as_telegram'],
            scheduled=False,
        )
    except Exception as e:
        ok = False
        print(f"❌ Inbox {msg.id}: исключение при выполнении: {e}")

    if ok is True:
        await _drop_command(main, dest, msg, 'выполнено')
        print(f"✅ Inbox {msg.id}: выполнено")
        return 'ok'
    print(f"❌ Inbox {msg.id}: НЕ выполнено")
    if await _drop_command(main, dest, msg, 'провал'):
        # Детали провала анализа уже в топике (error-path run_analysis) —
        # здесь однострочник, чтобы не ждали впустую.
        await _notify_inbox(main, dest, notify_topic,
                            f"⛔ Inbox {msg.id}: не выполнено — команда удалена, "
                            f"повтора не будет.\nКоманда: {snippet!r}")
    else:
        _log_once(mem, f"dropfail:{msg.id}",
                  f"⚠️ Inbox {msg.id}: команда не удалена — повторится следующим опросом")
    return 'fail'


async def poll_inbox_once(main, last_seen, mem):
    """Один опрос inbox-канала. Возвращает (new_last_seen, failed_count).

    Итерация отдаёт сообщения ВСЕХ топиков канала (не только General) —
    топик каждой команды определяет process_inbox_message по reply_to.
    last_seen=None → первый опрос: берём до INBOX_INITIAL_LIMIT свежих
    (покрывает команды из межранового зазора). Дальше — только id > last_seen.
    last_seen растёт всегда (in-memory, за ран).
    """
    dest = main.RESULTS_DESTINATION
    if last_seen is None:
        batch = [m async for m in main.telegram_client.iter_messages(dest, limit=INBOX_INITIAL_LIMIT)]
        batch = [m for m in batch if getattr(m, 'id', 0)]
        batch.sort(key=lambda m: m.id)
    else:
        batch = [m async for m in main.telegram_client.iter_messages(dest, min_id=last_seen)]
        batch.sort(key=lambda m: m.id)

    if not batch:
        return last_seen, 0
    new_last_seen = max(m.id for m in batch)

    failed = 0
    for msg in batch:
        if await process_inbox_message(main, mem, dest, msg) == 'fail':
            failed += 1

    return new_last_seen, failed


async def run_watch(main, args):
    """Цикл §1 плана: due-задачи + опрос inbox до дедлайна, disconnect один раз."""
    path = state_path()
    state = load_state(path)
    restore_key_cursor(main, state)

    try:
        await main.telegram_client.start(phone=main.PHONE)
    except Exception as e:
        print(f"❌ Не удалось подключиться к Telegram: {e}")
        print("   Проверьте TELEGRAM_SESSION / TELEGRAM_API_ID / TELEGRAM_API_HASH.")
        return 1

    failed_total = 0
    last_seen = None
    inbox_mem = new_inbox_mem()
    try:
        deadline = time.monotonic() + args.watch_seconds
        iteration = 0
        while True:
            iteration += 1
            print(f"─── Итерация {iteration} ───")
            failed_total += await run_due_once(main, args, state, path)
            try:
                last_seen, inbox_failed = await poll_inbox_once(main, last_seen, inbox_mem)
                failed_total += inbox_failed
            except Exception as e:
                print(f"⚠️ Ошибка опроса inbox (итерация {iteration}): {e}")
            now = time.monotonic()
            if now >= deadline:
                break
            await asyncio.sleep(min(args.poll_interval, deadline - now))
    finally:
        store_key_cursor(main, state)
        try:
            save_state(path, state)
        except Exception as e:
            print(f"⚠️ Не удалось сохранить state: {e}")
        try:
            await main.telegram_client.disconnect()
        except Exception as e:
            print(f"⚠️ Ошибка при disconnect: {e}")
        try:
            await main.http_client.aclose()
        except Exception as e:
            print(f"⚠️ Ошибка при закрытии http_client: {e}")

    print(f"🏁 Watch завершён ({iteration} итераций, неуспехов: {failed_total}).")
    return 1 if failed_total else 0


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
    path = state_path()
    state = load_state(path)
    restore_key_cursor(main, state)
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
        store_key_cursor(main, state)
        try:
            save_state(path, state)
        except Exception as e:
            print(f"⚠️ Не удалось сохранить state: {e}")
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

    # main.py на уровне модуля создаёт TelegramClient. Telethon 1.34 при
    # конструировании обращается к текущему event loop (свойство loop →
    # get_running_loop()), поэтому на Python 3.11+ без запущенного цикла
    # импорт падает с RuntimeError. Создаём цикл явно до импорта main.
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
    if args.watch:
        return asyncio.run(run_watch(main, args))
    return asyncio.run(run_manual(main, args))


if __name__ == '__main__':
    sys.exit(run(sys.argv[1:]))