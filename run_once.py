#!/usr/bin/env python3
"""Однократный запуск срочных заданий — точка входа для GitHub Actions (вариант B).

Запускается по cron каждые 10 минут, определяет, какие задания из SCHEDULE.txt
попали в окно [now - LAG_MAX, now] (время МСК), выполняет их через общее ядро
main.scheduled_analysis_job и дедуплицирует через state.json (ветка `state`).
В режиме --watch дополнительно опрашивает inbox (тема General канала
результатов): форвард + команда sum/copy → выполнение через main.run_analysis.

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
LAG_MAX_DEFAULT = 15    # минуты
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
    entries = main.load_schedule(main.SCHEDULE_FILE)
    due = compute_due(entries, now_naive, args.lag_max)
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

    try:
        await main.telegram_client.start(phone=main.PHONE)
    except Exception as e:
        print(f"❌ Не удалось подключиться к Telegram: {e}")
        print("   Проверьте TELEGRAM_SESSION / TELEGRAM_API_ID / TELEGRAM_API_HASH.")
        return 1

    try:
        failed = await run_due_once(main, args, state, path)
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


# ──────────────────────────────────────────────
# Inbox: форвард + команда sum/copy в General канала результатов
# ──────────────────────────────────────────────

INBOX_INITIAL_LIMIT = 100  # глубина первого опроса (покрывает межрановый зазор)
INBOX_PAIR_MAX_ID_DIST = 10   # спаривание: форвард не дальше N сообщений от команды
INBOX_PAIR_MAX_SECONDS = 600  # ...и не дальше 10 минут по времени
INBOX_PENDING_MAX = 20        # сколько неспаренных команд/форвардов помним внутри рана
INBOX_PENDING_MAX_AGE = 3600  # старше часа — забываем


def forward_source_id(msg):
    """ID источника из собственного форварда msg (канал/чат/пользователь) или None.

    PeerUser тоже годится: диалог анализируется как обычный чат.
    Без атрибуции (копипаст, запрет указания авторства) → None.
    """
    from telethon.tl.types import PeerChannel, PeerChat, PeerUser
    from telethon.utils import get_peer_id

    fwd = getattr(msg, 'fwd_from', None)
    peer = getattr(fwd, 'from_id', None) if fwd else None
    if isinstance(peer, (PeerChannel, PeerChat, PeerUser)):
        return get_peer_id(peer)
    return None


def find_pair_forward(cmd, forwards, consumed,
                      max_id_dist=INBOX_PAIR_MAX_ID_DIST,
                      max_seconds=INBOX_PAIR_MAX_SECONDS):
    """Спаривание (§2 п.3 плана): команда, а следующим сообщением — форвард.

    Порядок фиксированный: форвард строго НОВЕЕ команды (твой флоу: сначала
    пишешь команду, потом пересылаешь цитату). forwards — форварды-кандидаты
    (без своей команды); consumed — id уже использованных в этом ране.
    Побеждает ближайший сверху; возврат — сообщение-форвард или None.
    """
    best = None
    best_dist = None
    for fwd in forwards:
        if getattr(fwd, 'id', None) in consumed:
            continue
        if getattr(fwd, 'id', 0) <= cmd.id:
            continue  # форвард должен идти ПОСЛЕ команды, не до неё
        try:
            dist = fwd.id - cmd.id
            skew = abs((fwd.date - cmd.date).total_seconds())
        except Exception:
            continue
        if dist > max_id_dist or skew > max_seconds:
            continue
        if best is None or dist < best_dist:
            best, best_dist = fwd, dist
    return best


def resolve_inbox_source(msg):
    """Источник команды по строгому порядку (§2 плана).

    1. fwd_from.from_id (PeerChannel/PeerChat/PeerUser) самого сообщения.
    2. Иначе reply-родитель с fwd_from.from_id.
    Возвращает (source_chat_id | None, parent_msg | None).
    Отсутствие форварда → (None, ...) = не команда.
    """
    source = forward_source_id(msg)
    if source is not None:
        return source, None

    reply_id = getattr(msg, 'reply_to_msg_id', None)
    if reply_id:
        return None, reply_id  # родителя догрузит вызыватель (нужен async)
    return None, None


def new_inbox_mem():
    """Память inbox внутри одного рана: неспаренные команды/форварды + съеденные форварды."""
    return {'cmds': {}, 'fwds': {}, 'consumed': set()}


async def poll_inbox_once(main, last_seen, mem):
    """Один опрос inbox. Возвращает (new_last_seen, failed_count).

    last_seen=None → первый опрос: берём до INBOX_INITIAL_LIMIT свежих
    (покрывает команды из межранового зазора). Дальше — только id > last_seen.
    last_seen растёт всегда (in-memory, за ран), обработанные команды удаляются
    (дедуп персистить не надо).

    mem (из new_inbox_mem) помнит неспаренные команды/форварды между опросами:
    команда и форвард могут прийти в разные опросы (твой флоу: сначала команда,
    следующим сообщением — цитата). Успешная пара удаляется целиком, повторное
    использование форварда исключено (consumed + удаление).
    """
    dest = main.RESULTS_DESTINATION
    if last_seen is None:
        batch = [m async for m in main.telegram_client.iter_messages(dest, limit=INBOX_INITIAL_LIMIT)]
        batch = [m for m in batch if getattr(m, 'id', 0)]
        batch.sort(key=lambda m: m.id)
    else:
        batch = [m async for m in main.telegram_client.iter_messages(dest, min_id=last_seen)]
        batch.sort(key=lambda m: m.id)

    if not batch and not mem['cmds'] and not mem['fwds']:
        return last_seen, 0
    new_last_seen = last_seen
    if batch:
        new_last_seen = max([m.id for m in batch] + ([last_seen] if last_seen else []))
    fresh_ids = {m.id for m in batch}

    now = datetime.now(timezone.utc)

    def _fresh(m):
        try:
            d = m.date
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return (now - d).total_seconds() <= INBOX_PENDING_MAX_AGE
        except Exception:
            return True

    # Чистим память: протухшее и сверх лимита (старое — первым).
    for store in (mem['cmds'], mem['fwds']):
        for mid in [k for k, m in store.items() if not _fresh(m)]:
            del store[mid]
        while len(store) > INBOX_PENDING_MAX:
            store.pop(next(iter(store)))

    parsed_cache = {}

    def _parsed(m):
        if m.id not in parsed_cache:
            text = (getattr(m, 'text', None) or '').strip()
            try:
                parsed_cache[m.id] = main.parse_chat_command_args(text) if text else None
            except Exception as e:
                # Битый параметр ('sum abh'): не команда и не провал анализа —
                # пропускаем, не удаляем, опрос не отравляем (лог — раз на опрос).
                if m.id in fresh_ids:
                    print(f"⚠️  Inbox {m.id}: не удалось распарсить {text!r}: {e} — пропускаю")
                parsed_cache[m.id] = None
        return parsed_cache[m.id]

    # Запоминаем свежие кандидаты (команды — всегда; форварды — без своей команды).
    for m in batch:
        if not _fresh(m):
            continue
        if _parsed(m) is not None:
            mem['cmds'].setdefault(m.id, m)
        elif forward_source_id(m) is not None:
            mem['fwds'].setdefault(m.id, m)

    forwards_pool = sorted(mem['fwds'].values(), key=lambda m: m.id)

    failed = 0
    for cmd_id in sorted(mem['cmds']):
        msg = mem['cmds'][cmd_id]
        parsed = _parsed(msg)
        if parsed is None:
            mem['cmds'].pop(cmd_id, None)
            continue

        paired_fwd = None
        parent_id = None
        source_id, need_parent = resolve_inbox_source(msg)
        if need_parent is not None:
            parent_id = need_parent
            try:
                parent = await main.telegram_client.get_messages(dest, ids=need_parent)
                if isinstance(parent, list):
                    parent = parent[0] if parent else None
                if parent is not None:
                    source_id, _ = resolve_inbox_source(parent)
            except Exception as e:
                print(f"⚠️ Inbox {msg.id}: не удалось получить родителя {need_parent}: {e}")
                failed += 1
                continue
        if source_id is None:
            # п.3 плана: команда выше, форвард — следующим сообщением.
            paired_fwd = find_pair_forward(
                msg, forwards_pool, mem['consumed'])
            if paired_fwd is not None:
                source_id = forward_source_id(paired_fwd)
        if source_id is None:
            if msg.id in fresh_ids:
                if need_parent is not None:
                    print(f"⏭️  Inbox {msg.id}: команда без форварда источника "
                          f"(ответ на {need_parent}, у родителя нет форварда) — жду пару")
                else:
                    print(f"⏭️  Inbox {msg.id}: команда без форварда источника — жду пару")
            continue  # остаётся в памяти до следующего опроса

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
            print(f"❌ Inbox {msg.id}: нет доступа к чату {source_id}: {e} (повторю следующим опросом)")
            failed += 1
            continue

        use_ai = parsed['use_ai']
        via = f" (пара с форвардом {paired_fwd.id})" if paired_fwd is not None else ""
        print(f"▶️  Inbox {msg.id}: {'sum' if use_ai else 'copy'} из '{chat_name}' ({source_id}){via}")
        try:
            topic_id = await main.get_or_create_topic(chat_name)
            action = "анализ" if use_ai else "экспорт"
            await main.telegram_client.send_message(
                dest, f"🔄 Начинаю {action} по команде из inbox, чат '{chat_name}'...",
                reply_to=topic_id)
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
            to_delete = [msg.id] + ([paired_fwd.id] if paired_fwd is not None else [])
            try:
                await main.telegram_client.delete_messages(dest, to_delete)
                print(f"✅ Inbox {msg.id}: выполнено, удалено сообщений: {len(to_delete)}")
            except Exception as e:
                print(f"⚠️ Inbox {msg.id}: выполнено, но удалить не удалось: {e}")
            mem['cmds'].pop(cmd_id, None)
            if paired_fwd is not None:
                mem['fwds'].pop(paired_fwd.id, None)
                mem['consumed'].add(paired_fwd.id)
            if parent_id is not None:
                # Родитель-форвард уже отработал как источник — в пары не отдаём.
                mem['fwds'].pop(parent_id, None)
                mem['consumed'].add(parent_id)
        else:
            failed += 1
            print(f"❌ Inbox {msg.id}: НЕ выполнено — сообщение оставляю (будет повторено)")

    return new_last_seen, failed


async def run_watch(main, args):
    """Цикл §1 плана: due-задачи + опрос inbox до дедлайна, disconnect один раз."""
    path = state_path()
    state = load_state(path)

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