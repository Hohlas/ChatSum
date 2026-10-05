#!/usr/bin/env python3
"""Однократный запуск срочных заданий — точка входа для GitHub Actions.

Дежурный watch-цикл (лидер + standby через флаги в ветке state) опрашивает
inbox каждые ~30 сек и крутит due-задачи из SCHEDULE.txt. Слот due, если
в state.completed нет отметки за дату его последнего наступления (МСК), —
опоздание любой длины догоняется одним разом. Выполняет через общее ядро
main.scheduled_analysis_job / main.run_analysis.
В режиме --watch дополнительно опрашивает inbox (тема General канала
результатов): команда со ссылкой (`sum20 t.me/…`) либо команда в топике чата →
выполнение через main.run_analysis.

Режимы:
  python run_once.py --due                              # всё, что в окне
  python run_once.py --watch [--watch-seconds 18000] [--poll-interval 30]
  python run_once.py --chat-id -100... --period 1d [--post-source] [--post-tg]
  python run_once.py --list                             # напечатать расписание
"""

import argparse
import asyncio
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

# private.txt используем только на VPS; на Actions env приходит из секретов.
# override=False: уже заданные env (секреты Actions) не затираются.
load_dotenv('private.txt', override=False)

MSK = timezone(timedelta(hours=3))
STATE_DEFAULT = 'state.json'
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
    p.add_argument('--watch-seconds', type=int, default=18000,
                   help='Длительность цикла --watch, секунд (по умолчанию 18000 = 5 ч)')
    p.add_argument('--poll-interval', type=int, default=30,
                   help='Пауза между итерациями --watch, секунд (по умолчанию 30)')
    p.add_argument('--period', default='1d', help='Период анализа (1d, 12h)')
    p.add_argument('--post-source', action='store_true', help='Публиковать результат в исходном чате')
    p.add_argument('--post-tg', action='store_true', help='Отправлять как сообщение Telegram вместо Telegraph')
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
    """Оставляет в completed только записи за последние ~3 суток (МСК).

    Счётчики inbox_attempts/due_fails тоже стареют: записи старше 3 суток
    выкидываем, иначе state вечно пухнет от удалённых команд.
    """
    completed = state.setdefault('completed', {})
    if completed:
        cutoff = (datetime.now(MSK).date() - timedelta(days=STATE_RETENTION_DAYS)).isoformat()
        state['completed'] = {k: v for k, v in completed.items() if v >= cutoff}
    cutoff_utc = _utc_str(_utc_now() - timedelta(days=STATE_RETENTION_DAYS))
    for field, ts_key in (('inbox_attempts', 'ts'), ('due_fails', 'first_utc')):
        counters = state.get(field)
        if not isinstance(counters, dict):
            continue
        state[field] = {k: v for k, v in counters.items()
                        if not isinstance(v, dict) or str(v.get(ts_key) or '') >= cutoff_utc}


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


# ──────────────────────────────────────────────
# Дежурство: лидер + standby для длинных перекрывающихся ранов
#
# Крон тикает каждые 11 мин, а watch-цикл живёт до 5 ч. Роли распределяются
# двумя флагами в ветке state (через Contents API, без checkout посреди
# работы): leader.json — кто опрашивает inbox и крутит due; standby.json —
# кто лёгким поллингом (без Telegram) следит за heartbeat лидера.
# Новорождённый ран при живом лидере НЕ вытесняет его, а становится standby
# (вытесняя более старый standby). Standby promotes себя в лидеры, если флаг
# лидера пропал, протух (старше LEADER_STALE_SEC) или помечен retiring
# (лидер грациозно уходит на дедлайне) — подтверждение двумя подряд
# протухшими чтениями против ложных срабатываний. Два живых лидера
# невозможны дольше итерации: правило «новее побеждает», старший выходит.
# ──────────────────────────────────────────────

LEADER_FILE = 'leader.json'
STANDBY_FILE = 'standby.json'
LEADER_STALE_SEC = 300       # лидер без heartbeat дольше этого — мёртв
LEADER_HEARTBEAT_SEC = 120   # период heartbeat + push state
SCHEDULE_REFRESH_SEC = 600   # период обновления SCHEDULE.txt из origin/main
PROMOTE_CONFIRM_READS = 2    # подряд протухших чтений перед promotion
INBOX_MAX_ATTEMPTS = 5       # попыток inbox-команды, дальше — удалить
DUE_FAIL_THRESHOLD = 3       # подряд провалов due-ключа до пропуска
DUE_FAIL_SKIP_SEC = 10800    # пропуск падающего due-ключа: 3 часа
MAX_DUE_PER_ITERATION = 1    # due-саммари за итерацию: inbox опрашивается первым
                             # каждую итерацию, догон расписания идёт по одному —
                             # иначе шторм догона (4–6 слотов) хоронит команды

try:
    GAP_WARN_SEC = int((os.getenv('GAP_WARN_SEC', '') or '').strip() or 240)
except ValueError:
    GAP_WARN_SEC = 240           # тишина дежурства дольше этого — варнинг в General
                                # (240: ловит аварийные зазоры от 300с, чистые
                                # передачи через retiring — секунды — молчат)
try:
    WORK_STALE_SEC = int((os.getenv('WORK_STALE_SEC', '') or '').strip() or 900)
except ValueError:
    WORK_STALE_SEC = 900         # лидер без прогресса дольше этого — завис:
                                # heartbeat свеж, а работа стоит (сессия 12).
                                # 15 мин >> худшего честного чанка (~10-12 мин:
                                # 3×180с таймаута + ретраи + ротации), << вотча
WORK_FILE = 'work.json'         # маркер прогресса {run_id, last_work_utc}


def handoff_enabled():
    """Есть ли доступ к API для флага (GITHUB_TOKEN + GITHUB_REPOSITORY)."""
    return bool(os.getenv('GITHUB_TOKEN', '').strip()
                and os.getenv('GITHUB_REPOSITORY', '').strip())


def my_run_id():
    run_id = os.getenv('GITHUB_RUN_ID', '').strip()
    if run_id:
        return f"{run_id}/{os.getenv('GITHUB_RUN_ATTEMPT', '').strip() or '1'}"
    return f"local-{uuid.uuid4().hex[:8]}"


def _utc_now():
    return datetime.now(timezone.utc)


def _utc_str(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse_utc(raw):
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def new_leader_doc(run_id):
    now = _utc_str(_utc_now())
    return {'run_id': run_id, 'ready': False,
            'heartbeat_utc': now, 'watch_started_utc': now}


def leader_is_live(doc, now=None):
    """Свежий ли heartbeat (мёртвых лидеров игнорируем)."""
    if not isinstance(doc, dict):
        return False
    hb = _parse_utc(doc.get('heartbeat_utc'))
    if hb is None:
        return False
    now = now or _utc_now()
    return (now - hb).total_seconds() < LEADER_STALE_SEC


def leader_should_yield(mine, remote, now=None):
    """Уступить ли лидерство чужому флагу (чистая логика, тестируется).

    Уступаем только живому ГОТОВОМУ более новому лидеру. Своему run_id,
    мёртвому и ещё стартующему (ready=false) — не уступаем: стартущий
    никого не вытесняет, пока не поднимется. Равные watch_started
    (старт в одну секунду) решает больший run_id — иначе уступят оба
    и дежурство останется без лидера до следующего тика.
    """
    if not isinstance(remote, dict):
        return False
    if remote.get('run_id') == (mine or {}).get('run_id'):
        return False
    if not leader_is_live(remote, now):
        return False
    if not remote.get('ready'):
        return False
    rs = str(remote.get('watch_started_utc') or '')
    ms = str((mine or {}).get('watch_started_utc') or '')
    if rs != ms:
        return rs >= ms
    return str(remote.get('run_id') or '') > str((mine or {}).get('run_id') or '')


def duty_gap_info(last_hb_utc, now=None, threshold_sec=GAP_WARN_SEC):
    """Самодиагностика пропусков: (gap_sec|None, warn: bool).

    last_hb_utc — последний чужой heartbeat (ISO/None/мусор). Возвращает
    зазор в секундах и флаг варнинга (строго больше порога). Базы нет,
    мусор, зазор в будущем/отрицательный — (None, False): молчим.
    """
    hb = _parse_utc(last_hb_utc)
    if hb is None:
        return None, False
    now = now or _utc_now()
    gap = (now - hb).total_seconds()
    if gap <= 0:
        return None, False
    return int(gap), gap > threshold_sec


def format_gap_warning(gap_sec, last_hb_iso):
    """Короткий текст варнинга о пропуске: две строки, время — МСК.

    Чистая логика, тестируется. Мусор на входе — сырая строка как есть.
    """
    mins = gap_sec // 60
    hb = _parse_utc(last_hb_iso)
    stamp = hb.astimezone(MSK).strftime('%Y-%m-%d %H:%M:%S') if hb else last_hb_iso
    return f"⚠️ Дежурство прерывалось на {mins} мин.\nПоследний heartbeat {stamp}"


LIVENESS_PREFIX = 'heartbeat '  # префикс маяка дежурства в General
LIVENESS_EVERY = 5              # каждый 5-й тик heartbeat (120с × 5 = 10 мин)
LIVENESS_SCAN_LIMIT = 50        # глубина поиска маяка при заступлении

_liveness_msg_id = None  # in-memory id маяка текущего лидера (сессия 20).
                         # В me не кладём: leader_claim сериализует весь me
                         # во флаг, чужеродное поле там не нужно.
_liveness_next_utc = None  # ISO UTC ожидаемого конца вахты (строка next leader).


def format_liveness_text(now=None, next_utc=None):
    """Текст маяка дежурства: время + ожидаемый конец вахты, всё МСК.

    Чистая логика, тестируется. Без next_utc — одна строка (старт лидера,
    конец вахты ещё не посчитан). next_utc — ISO UTC или aware datetime.
    """
    dt = now or _utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    lines = [f"heartbeat {dt.astimezone(MSK).strftime('%d.%m %H:%M')}"]
    nxt = _parse_utc(next_utc) if isinstance(next_utc, str) else next_utc
    if nxt is not None:
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        lines.append(f"next leader: {nxt.astimezone(MSK).strftime('%d.%m %H:%M')}")
    return '\n'.join(lines)


def liveness_set_next(end_utc_iso):
    """Запомнить ожидаемый конец вахты (in-memory, для строки next leader)."""
    global _liveness_next_utc
    _liveness_next_utc = end_utc_iso


def is_liveness_text(text):
    """Свой ли это маяк (по префиксу). Для inbox безвреден и так:
    parse_chat_command_args вернёт None → 'skip', сообщение не трогаем."""
    return isinstance(text, str) and text.startswith(LIVENESS_PREFIX)


async def liveness_find(main, dest=None):
    """Найти id последнего маяка в General (подхват при смене дежурного).

    Возвращает max id среди своих сообщений с префиксом, иначе None.
    Best-effort: неуспех — None, вызыватель запостит новый.
    """
    try:
        dest = dest or main.RESULTS_DESTINATION
        best = None
        async for m in main.telegram_client.iter_messages(dest, limit=LIVENESS_SCAN_LIMIT):
            if is_liveness_text(getattr(m, 'text', None)) and getattr(m, 'id', 0):
                if best is None or m.id > best:
                    best = m.id
        return best
    except Exception as e:
        print(f"⚠️ Маяк: не удалось найти прошлое сообщение: {e}")
        return None


async def liveness_post(main, dest=None):
    """Опубликовать новый маяк. Возвращает msg_id|None. Best-effort."""
    try:
        dest = dest or main.RESULTS_DESTINATION
        msg = await main.telegram_client.send_message(
            dest, format_liveness_text(next_utc=_liveness_next_utc))
        mid = getattr(msg, 'id', None)
        print(f"💓 Маяк: опубликован ({mid})")
        return mid
    except Exception as e:
        print(f"⚠️ Маяк: не удалось опубликовать: {e}")
        return None


async def liveness_ensure(main, dest=None):
    """Подхват поиском или пост нового при заступлении. Возвращает msg_id|None.

    Новое сообщение — только если старого нет (первый запуск, удалено
    вручную): обычный handoff подхватывает чужой маяк и правит его.
    """
    global _liveness_msg_id
    try:
        found = await liveness_find(main, dest)
        if found:
            print(f"💓 Маяк: подхватил {found}")
            _liveness_msg_id = found
            return found
        _liveness_msg_id = await liveness_post(main, dest)
        return _liveness_msg_id
    except Exception as e:
        print(f"⚠️ Маяк: не удалось подняться: {e}")
        return None


async def liveness_tick(main, dest=None):
    """Одна плановая правка маяка (каждый N-й тик heartbeat).

    Возвращает актуальный msg_id. Правка упала (сообщение удалили) —
    один перепост вместо правки. Ран не роняем никогда.
    """
    global _liveness_msg_id
    try:
        dest = dest or main.RESULTS_DESTINATION
        if _liveness_msg_id:
            try:
                await main.telegram_client.edit_message(
                    dest, _liveness_msg_id,
                    format_liveness_text(next_utc=_liveness_next_utc))
                return _liveness_msg_id
            except Exception as e:
                print(f"⚠️ Маяк {_liveness_msg_id}: не удалось править "
                      f"({e}) — перепощу")
        _liveness_msg_id = await liveness_post(main, dest)
        return _liveness_msg_id
    except Exception as e:
        print(f"⚠️ Маяк: тик не удался: {e}")
        return _liveness_msg_id


def prev_duty_heartbeat(exclude_run_id):
    """Последний ЧУЖОЙ heartbeat дежурства: (hb_iso|None, run_id|None).

    Берёт свежее из флагов лидера и standby. Свой run_id исключаем:
    иначе standby, принимающий дежурство в том же ране, своими же
    heartbeat'ами замазал бы реальную дыру предшественника.
    """
    best_hb, best_run = None, None
    for reader in (leader_read, standby_read):
        try:
            doc, _ = reader()
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        if doc.get('run_id') == exclude_run_id:
            continue
        hb = _parse_utc(doc.get('heartbeat_utc'))
        if hb is None:
            continue
        if best_hb is None or hb > _parse_utc(best_hb):
            best_hb, best_run = doc.get('heartbeat_utc'), doc.get('run_id')
    return best_hb, best_run


def work_read():
    """Прочитать чужой маркер прогресса: (doc|None, sha|None)."""
    return _flag_read(WORK_FILE)


def work_claim(doc):
    """Записать свой маркер прогресса (last-writer-wins, один ретрай при гонке)."""
    return _flag_claim(WORK_FILE, doc, 'work')


_last_work_utc = None  # локальная метка последнего прогресса (итерация/чанк)


def note_work_progress():
    """Отметить прогресс дежурства (дёргается из итераций и между чанками)."""
    global _last_work_utc
    _last_work_utc = _utc_str(_utc_now())


def push_work_best_effort(run_id):
    """Опубликовать маркер прогресса (best-effort: неуспех ран не роняет)."""
    global _last_work_utc
    if _last_work_utc is None:
        note_work_progress()
    try:
        work_claim({'run_id': run_id, 'last_work_utc': _last_work_utc})
    except Exception as e:
        print(f"⚠️ Не удалось опубликовать маркер прогресса: {e}")


def work_is_wedged(work_doc, leader_doc, now=None, threshold_sec=WORK_STALE_SEC):
    """Завис ли лидер: heartbeat свеж, а прогресса нет дольше порога.

    Чистая логика, тестируется. True — только если маркер есть, принадлежит
    ТЕКУЩЕМУ лидеру и протух. Нет маркера (старый код) / чужой run_id /
    мусор / будущее — False: по отсутствию данных никого не свергаем.
    """
    if not isinstance(work_doc, dict) or not isinstance(leader_doc, dict):
        return False
    if work_doc.get('run_id') != leader_doc.get('run_id'):
        return False
    age = now_or_age(work_doc.get('last_work_utc'), now)
    if age is None:
        return False
    return age > threshold_sec


def now_or_age(raw, now=None):
    """Возраст метки в секундах; мусор/будущее → None."""
    ts = _parse_utc(raw)
    if ts is None:
        return None
    age = ((now or _utc_now()) - ts).total_seconds()
    return age if age > 0 else None


def leader_watch_seconds(watch_remaining, watch_seconds):
    """Сколько секунд ведёт лидер: остаток своего вотча или полный вотч.

    Чистая логика, тестируется. Свежий лидер (remaining=None) берёт полный
    вотч; promoted standby наследует остаток собственного дедлайна (сессия 13):
    иначе свежий полный вотч переживает джоб и ротация идёт через килл.
    Остаток ≤0 — сразу retiring (но через одну полезную итерацию: цикл
    проверяет дедлайн после inbox+due, а не до).
    """
    if watch_remaining is None:
        return watch_seconds
    return max(0.0, watch_remaining)


def standby_should_promote(remote, consec_stale_reads, now=None,
                           required=PROMOTE_CONFIRM_READS):
    """Пора ли standby забирать лидерство (чистая логика, тестируется).

    Да — если флага нет вообще, лидер объявил retiring (уходит на дедлайне)
    или heartbeat протух подряд `required` чтений (защита от одиночного
    сбоя сети: один протухший GET ещё не смерть).
    """
    if not isinstance(remote, dict):
        return True
    if remote.get('retiring'):
        return True
    if not leader_is_live(remote, now):
        return consec_stale_reads >= required
    return False


def _flag_claim(flag_file, doc, label):
    """Записать свой флаг (last-writer-wins, один ретрай при гонке sha)."""
    _, sha = gh_state_file_get(flag_file)
    doc_text = json.dumps(doc if isinstance(doc, dict) else {}, ensure_ascii=False)
    if gh_state_file_put(flag_file, doc_text, sha,
                         message=f"{label}: {doc.get('run_id') if isinstance(doc, dict) else '?'}"):
        return True
    _, sha2 = gh_state_file_get(flag_file)
    return gh_state_file_put(flag_file, doc_text, sha2,
                             message=f"{label}: "
                             f"{doc.get('run_id') if isinstance(doc, dict) else '?'} (retry)")


def _flag_read(flag_file):
    """Прочитать чужой флаг: (doc|None, sha|None)."""
    doc, sha = gh_state_file_get(flag_file)
    return (doc if isinstance(doc, dict) else None), sha


def _gh_contents_url(path, for_write=False):
    api = os.getenv('GITHUB_API_URL', 'https://api.github.com').rstrip('/')
    repo = os.getenv('GITHUB_REPOSITORY', '').strip()
    url = f"{api}/repos/{repo}/contents/{path}"
    return url if for_write else f"{url}?ref=state"


def _gh_headers():
    return {'Authorization': f"Bearer {os.getenv('GITHUB_TOKEN', '').strip()}",
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28'}


def gh_state_file_get(path):
    """Прочитать файл из ветки state. Возвращает (obj|str|None, sha|None).

    404 (нет ветки/файла) и сетевые ошибки — (None, None), вызыватель
    трактует как «флага нет», ран продолжает соло. Ран не роняем никогда.
    """
    req = urllib.request.Request(_gh_contents_url(path), headers=_gh_headers())
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        if getattr(e, 'code', None) != 404:
            print(f"⚠️ Handoff: GET {path} → HTTP {e.code}")
        return None, None
    except Exception as e:
        print(f"⚠️ Handoff: GET {path} не удался: {e}")
        return None, None
    try:
        raw = base64.b64decode(payload.get('content') or '').decode('utf-8')
    except Exception as e:
        print(f"⚠️ Handoff: {path} не декодируется: {e}")
        return None, None
    if path.endswith('.json'):
        try:
            return json.loads(raw or '{}'), payload.get('sha')
        except Exception:
            return {}, payload.get('sha')
    return raw, payload.get('sha')


def gh_state_file_put(path, text, sha=None, message=None):
    """Записать файл в ветку state. Возвращает True/False (ран не роняет)."""
    body = json.dumps({
        'message': message or f"chatsum: {path} {my_run_id()}",
        'content': base64.b64encode(text.encode('utf-8')).decode(),
        'branch': 'state',
        **({'sha': sha} if sha else {}),
    }).encode()
    req = urllib.request.Request(
        _gh_contents_url(path, for_write=True), data=body,
        headers={**_gh_headers(), 'Content-Type': 'application/json'}, method='PUT')
    try:
        with urllib.request.urlopen(req, timeout=20):
            return True
    except urllib.error.HTTPError as e:
        print(f"⚠️ Handoff: PUT {path} → HTTP {e.code}")
        return False
    except Exception as e:
        print(f"⚠️ Handoff: PUT {path} не удался: {e}")
        return False


def leader_read():
    """Прочитать чужой флаг лидера: (doc|None, sha|None)."""
    return _flag_read(LEADER_FILE)


def leader_claim(doc):
    """Записать свой флаг лидера (last-writer-wins, один ретрай при гонке sha)."""
    return _flag_claim(LEADER_FILE, doc, 'leader')


def standby_read():
    """Прочитать чужой флаг standby: (doc|None, sha|None)."""
    return _flag_read(STANDBY_FILE)


def standby_claim(doc):
    """Записать свой флаг standby (last-writer-wins, один ретрай при гонке sha)."""
    return _flag_claim(STANDBY_FILE, doc, 'standby')


def merge_completed(local_completed, remote_completed):
    """Объединение dedup-карт: побеждает свежая дата (строки ISO сравнимы)."""
    merged = dict(remote_completed or {})
    for k, v in (local_completed or {}).items():
        if k not in merged or str(v) >= str(merged[k]):
            merged[k] = v
    return merged


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def merge_counters(local_counters, remote_counters):
    """Объединение счётчиков {key: {'n': int, ...}}: побеждает большее n.

    Монотонность важна для pull-merge перед финальным save: запись поверх
    свежей ветки обязана быть надмножеством, иначе Persist затёр бы прогресс
    лидера (баг «молчаливый standby перетирает state»).
    """
    merged = {k: dict(v) if isinstance(v, dict) else {'n': v}
              for k, v in (remote_counters or {}).items()}
    for k, v in (local_counters or {}).items():
        v = dict(v) if isinstance(v, dict) else {'n': v}
        cur = merged.get(k)
        cur_n = cur.get('n', 0) if isinstance(cur, dict) else 0
        try:
            new_n = int(v.get('n', 0))
        except (TypeError, ValueError):
            new_n = 0
        if k not in merged or new_n >= cur_n:
            merged[k] = v
    return merged


def merge_states(local, remote):
    """Union двух state-словарей в пользу свежих/больших значений (pure)."""
    merged = dict(local or {})
    remote = remote or {}
    merged['completed'] = merge_completed(_as_dict((local or {}).get('completed')),
                                          _as_dict(remote.get('completed')))
    merged['inbox_attempts'] = merge_counters(_as_dict((local or {}).get('inbox_attempts')),
                                              _as_dict(remote.get('inbox_attempts')))
    merged['due_fails'] = merge_counters(_as_dict((local or {}).get('due_fails')),
                                         _as_dict(remote.get('due_fails')))
    if str(remote.get('last_run_utc') or '') > str(merged.get('last_run_utc') or ''):
        merged['last_run_utc'] = remote['last_run_utc']
    # Курсор ротации ключей: побеждает большее значение (иначе гонка двух
    # лидеров откатывала бы счётчик и ключи ходили бы по кругу повторно).
    try:
        merged['google_key_cursor'] = max(int(merged.get('google_key_cursor', 0) or 0),
                                         int(remote.get('google_key_cursor', 0) or 0))
    except (TypeError, ValueError):
        pass
    return merged


def _read_local_state_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def pull_state_best_effort(path):
    """Подтянуть state из origin/state в локальный файл (union всего).

    Нужно преемнику при handoff и обязательно перед финальным save:
    запись поверх свежей ветки обязана быть надмножеством, иначе Persist
    затёр бы чужой прогресс (молчаливый standby, уходящий лидер).
    """
    local = _read_local_state_json(path)
    remote, _ = gh_state_file_get(STATE_DEFAULT)
    if not isinstance(remote, dict):
        return
    try:
        save_state(path, merge_states(local, remote))
    except Exception as e:
        print(f"⚠️ Не удалось сохранить state после pull: {e}")


def push_state_best_effort(path):
    """Запушить локальный state в origin/state (merge при гонке sha).

    После успешной записи — проверка чтением: перечитываем ветку и убеждаемся,
    что все локальные completed-ключи на месте (растяжка на случай немой потери
    записи API, как 2026-10-04 утром: 5 ✅ в логе, в ветке осел 1 ключ).
    Не сошлось — один повторный PUT + громкий лог. Ран не роняем никогда.
    """
    local = _read_local_state_json(path)
    if not local:
        return False

    remote, sha = gh_state_file_get(STATE_DEFAULT)
    text = json.dumps(merge_states(local, remote) if isinstance(remote, dict) else local,
                      ensure_ascii=False, indent=2)
    if gh_state_file_put(STATE_DEFAULT, text, sha, message=f"state: {my_run_id()}"):
        return _verify_push(path, local)
    remote2, sha2 = gh_state_file_get(STATE_DEFAULT)  # гонка sha — один ретрай
    text2 = json.dumps(merge_states(local, remote2) if isinstance(remote2, dict) else local,
                       ensure_ascii=False, indent=2)
    if gh_state_file_put(STATE_DEFAULT, text2, sha2, message=f"state: {my_run_id()} (retry)"):
        return _verify_push(path, local)
    return False


def _verify_push(path, local):
    """Сверить ветку с локальным файлом. Возвращает True, если все локальные
    completed-ключи видны в ветке; иначе — повторный PUT и False/True по итогу."""
    want = set((_as_dict(local.get('completed'))).keys())
    if not want:
        return True
    remote, _ = gh_state_file_get(STATE_DEFAULT)
    if isinstance(remote, dict):
        have = set(_as_dict(remote.get('completed')).keys())
        if want <= have:
            return True
        missing = sorted(want - have)
        print(f"🚨 State-push не прижился (нет ключей {missing}) — повторный PUT")
        merged = json.dumps(merge_states(local, remote), ensure_ascii=False, indent=2)
        _, sha = gh_state_file_get(STATE_DEFAULT)
        if gh_state_file_put(STATE_DEFAULT, merged, sha,
                             message=f"state: {my_run_id()} (verify-retry)"):
            return True
        print("🚨 State-push: повтор тоже не подтверждён — метки только локально, "
              "их заберёт финальный Persist шага workflow")
    else:
        print("🚨 State-push: не удалось перечитать ветку для проверки")
    return False


def refresh_schedule_best_effort(main):
    """Обновить локальный SCHEDULE.txt из origin/main.

    За 5-часовой ран файл протухает: правки владельца и других ранов
    иначе не видны. Локальных незапушенных правок у дежурного не бывает
    (_sched_add/_sched_unsch пушат сразу либо откатывают), так что
    перезапись безопасна. Возвращает True/False.
    """
    import subprocess
    try:
        f = subprocess.run(['git', 'fetch', '--quiet', '--depth=1', 'origin', 'main'],
                           capture_output=True, text=True, timeout=60)
        if f.returncode != 0:
            return False
        s = subprocess.run(['git', 'show', 'origin/main:SCHEDULE.txt'],
                           capture_output=True, text=True, timeout=30)
        if s.returncode != 0 or not s.stdout.strip():
            return False
        text = s.stdout if s.stdout.endswith('\n') else s.stdout + '\n'
        with open(main.SCHEDULE_FILE, 'w', encoding='utf-8') as fh:
            fh.write(text)
        return True
    except Exception:
        return False


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
# Должные задания: персональный водяной знак (чистая логика, тестируется)
# ──────────────────────────────────────────────

def compute_due(entries, now_msk_naive, completed=None):
    """Возвращает [(entry, occurrence_naive_msk, date_key)] для необработанных.

    now_msk_naive — текущее время МСК (naive). occurrence — последнее
    наступление HH:MM не позже now; date_key — его МСК-дата.
    Слот due, если в completed нет отметки именно за эту дату наступления.
    Глобального окна/лага НЕТ осознанно: опоздание на 5 часов или сутки
    всё равно догоняется одним разом (период считается на момент
    выполнения, повторный прогон свежих данных безвреден). Больше одного
    наступления на ключ за ран не бывает — «шторма повторов» нет.
    """
    now = now_msk_naive.replace(second=0, microsecond=0)
    completed = completed or {}
    due = []
    for entry in entries:
        slot = now.replace(hour=entry['hour'], minute=entry['minute'])
        occurrence = slot if slot <= now else slot - timedelta(days=1)
        date_key = occurrence.strftime('%Y-%m-%d')
        if completed.get(task_key(entry)) != date_key:
            due.append((entry, occurrence, date_key))
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


def due_skip_info(due_fails, key, now=None):
    """Пропустить ли due-ключ: 3+ подряд провалов и не прошло 3 ч с первого.

    Без этого постоянно падающий чат жёг бы Gemini каждые 30 сек весь 5-часовой
    ран. Успех сбрасывает счётчик (см. run_due_once). Чистая логика.
    """
    rec = (due_fails or {}).get(key)
    if not isinstance(rec, dict):
        return False
    try:
        n = int(rec.get('n', 0))
    except (TypeError, ValueError):
        return False
    if n < DUE_FAIL_THRESHOLD:
        return False
    first = _parse_utc(rec.get('first_utc'))
    if first is None:
        return False
    now = now or _utc_now()
    return (now - first).total_seconds() < DUE_FAIL_SKIP_SEC


async def run_due_once(main, args, state, path, max_tasks=None):
    """Одна итерация due-задач: compute → dedup → выполнение → save.

    Соединение уже установлено вызывателем (run_due / run_watch).
    Отметка completed — строго при ok is True (флаг в самом конце).
    Провал — счётчик due_fails (общий через API-push), пропуск 3 ч после
    трёх подряд провалов. max_tasks ограничивает число задач за вызов
    (watch ставит MAX_DUE_PER_ITERATION, чтобы догон не хоронил inbox;
    None = все, для ручного --due). Возвращает число невыполненных задач.
    """
    now_msk = datetime.now(MSK)
    now_naive = now_msk.replace(second=0, microsecond=0)
    entries = main.load_schedule(main.SCHEDULE_FILE)
    due = compute_due(entries, now_naive, state.get('completed'))
    if not due:
        print(f"[{now_msk.strftime('%Y-%m-%d %H:%M:%S')} МСК] Нет необработанных слотов.")
        return 0

    completed = state.setdefault('completed', {})
    due_fails = state.setdefault('due_fails', {})

    pending = []
    for entry, occurrence, date_key in due:
        key = task_key(entry)
        if completed.get(key) == date_key:
            print(f"⏭️  Уже выполнено: {key} ({date_key})")
            continue
        if due_skip_info(due_fails, key):
            print(f"⏭️  Пропускаю до восстановления: {key} "
                  f"({due_fails[key].get('n')} провалов подряд)")
            continue
        pending.append((entry, occurrence, key, date_key))

    if not pending:
        print("Все должные задания выполнены или на backoff.")
        return 0

    if max_tasks is not None and len(pending) > max_tasks:
        print(f"⏳ Due-кап: беру {max_tasks} из {len(pending)}, "
              f"остальные — следующими итерациями (inbox вперёд).")
        pending = pending[:max_tasks]

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
            state['last_run_utc'] = _utc_str(_utc_now())
            due_fails.pop(key, None)
            prune_state(state)
            save_state(path, state)
            # Длинный ран: сразу делимся прогрессом с преемником (best-effort).
            if handoff_enabled():
                push_state_best_effort(path)
            print(f"✅ {key} выполнено, записано в state")
        else:
            failed += 1
            rec = due_fails.get(key)
            if not isinstance(rec, dict) or not rec.get('first_utc'):
                rec = {'n': 0, 'first_utc': _utc_str(_utc_now())}
            try:
                rec['n'] = int(rec.get('n', 0)) + 1
            except (TypeError, ValueError):
                rec['n'] = 1
            due_fails[key] = rec
            save_state(path, state)
            if handoff_enabled():
                push_state_best_effort(path)
            print(f"❌ {key} НЕ выполнено (провал {rec['n']}) — повтор позже")
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
FIRST_SWEEP_MAX_PAGES = 10  # потолок глубокой пагинации первого прохода (10×100)

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
    """Память inbox внутри одного рана: кэши топиков/диалогов + антиспам лога.

    Вызыватель-лидер дополнительно кладёт mem['state'] = state (тот же dict):
    счётчик попыток команд переживает handoff через state.json. Без 'state'
    (юнит-тесты) — старое поведение: провал удаляет команду сразу.
    """
    return {'topics': None, 'dialogs': None, 'skip_logged': set()}


def _inbox_attempts(mem):
    """Словарь попыток из state (или None, если mem без state)."""
    st = (mem or {}).get('state')
    if not isinstance(st, dict):
        return None
    return st.setdefault('inbox_attempts', {})


def _inbox_state_save(mem):
    """Сохранить state после изменения счётчика (файл + API-push преемнику)."""
    st = (mem or {}).get('state')
    if not isinstance(st, dict):
        return
    try:
        save_state(state_path(), st)
    except Exception as e:
        print(f"⚠️ Не удалось сохранить state (попытки): {e}")
        return
    if handoff_enabled():
        push_state_best_effort(state_path())


def _attempt_bump(mem, msg_id):
    """Зарегистрировать провал: возвращает (n, exhausted).

    Без state в mem — (1, True): старое поведение, финал сразу.
    Иначе n растёт до INBOX_MAX_ATTEMPTS, exhausted на пределе.
    """
    att = _inbox_attempts(mem)
    if att is None:
        return 1, True
    key = str(msg_id)
    rec = att.get(key)
    if not isinstance(rec, dict):
        rec = {}
    try:
        n = int(rec.get('n', 0)) + 1
    except (TypeError, ValueError):
        n = 1
    rec['n'] = n
    rec['ts'] = _utc_str(_utc_now())
    att[key] = rec
    return n, n >= INBOX_MAX_ATTEMPTS


def _attempt_clear(mem, msg_id):
    """Сбросить счётчик после успеха (команда выполнена и удаляется)."""
    att = _inbox_attempts(mem)
    if att is None:
        return
    att.pop(str(msg_id), None)


def _attempt_number(mem, msg_id):
    """Номер предстоящей попытки (для прогресс-сообщения)."""
    att = _inbox_attempts(mem)
    if att is None:
        return 1
    rec = att.get(str(msg_id))
    if not isinstance(rec, dict):
        return 1
    try:
        return int(rec.get('n', 0)) + 1
    except (TypeError, ValueError):
        return 1


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
    """Обработка одного inbox-сообщения. Возвращает 'ok' | 'fail' | 'skip' | 'retry'.

    Источник команды (строго по порядку): явная ссылка → топик (только если
    msg_topic_id указывает на известный топик канала результатов; заголовок
    есть у сообщений ВСЕХ топиков — опрос их не разделяет). Всё остальное —
    General: резолв по названию чата, иначе провал.
    Успех — удаление команды. Провал — счётчик попыток в state (до
    INBOX_MAX_ATTEMPTS команда НЕ удаляется и повторяется; смерть процесса
    посреди анализа — тоже повтор, сообщение переживает смерть). Исчерпание —
    удаление + диагностика. Без mem['state'] (тесты) — старое поведение:
    провал удаляет сразу. 'retry' в счёт failed_total не входит.
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
        n, exhausted = _attempt_bump(mem, msg.id)
        _inbox_state_save(mem)
        if not exhausted:
            print(f"{log_text} | {snippet!r} "
                  f"(попытка {n}/{INBOX_MAX_ATTEMPTS} — команда оставлена для повтора)")
            return 'retry'
        print(f"{log_text} | {snippet!r} (попытки исчерпаны: {n})")
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

    # Handoff-гонка: пир мог взять ту же команду секундами раньше (оба рана
    # видят её до удаления). Перечитываем: сообщения уже нет — значит, взято
    # пиром, пропускаем без повтора. Ошибка проверки — продолжаем (fail-open).
    try:
        still = await main.telegram_client.get_messages(dest, ids=msg.id)
        if not still:
            print(f"⏭️ Inbox {msg.id}: команда уже взята другим раном — пропускаю")
            return 'skip'
    except Exception as e:
        print(f"⚠️ Inbox {msg.id}: не удалось перепроверить команду ({e}) — продолжаю")

    notify_topic = home
    attempt_no = _attempt_number(mem, msg.id)
    retry_note = f" 🔁 Попытка {attempt_no}/{INBOX_MAX_ATTEMPTS}." if attempt_no > 1 else ""
    try:
        topic_out = await main.get_or_create_topic(chat_name)
        notify_topic = topic_out
        action = "анализ" if use_ai else "экспорт"
        await main.telegram_client.send_message(
            dest, f"🔄 Начинаю {action} по команде из inbox, чат '{chat_name}'...{retry_note}",
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
        _attempt_clear(mem, msg.id)
        _inbox_state_save(mem)
        await _drop_command(main, dest, msg, 'выполнено')
        print(f"✅ Inbox {msg.id}: выполнено")
        return 'ok'
    print(f"❌ Inbox {msg.id}: НЕ выполнено")
    n, exhausted = _attempt_bump(mem, msg.id)
    _inbox_state_save(mem)
    if not exhausted:
        print(f"⏳ Inbox {msg.id}: попытка {n}/{INBOX_MAX_ATTEMPTS} — команда оставлена для повтора")
        return 'retry'
    if await _drop_command(main, dest, msg, 'провал'):
        # Детали провала анализа уже в топике (error-path run_analysis) —
        # здесь однострочник, чтобы не ждали впустую.
        await _notify_inbox(main, dest, notify_topic,
                            f"⛔ Inbox {msg.id}: не выполнено после {n} попыток — "
                            f"команда удалена.\nКоманда: {snippet!r}")
    else:
        _log_once(mem, f"dropfail:{msg.id}",
                  f"⚠️ Inbox {msg.id}: команда не удалена — повторится следующим опросом")
    return 'fail'


async def _process_batch(main, mem, dest, batch):
    """Прогнать батч по process_inbox_message.

    Возвращает (failed, new_last_seen). 'retry' (попытка записана, команда
    оставлена) провалом не считается — ран остаётся зелёным, пока идёт
    борьба. new_last_seen откатывается к (старейший retry − 1), чтобы
    оставленные команды перечитались уже следующим опросом (через ~30 сек),
    а не только после рестарта рана. Повторно подхваченные поглощённые
    сообщения безопасны: команды-однодневки уже удалены, не-команды — skip.
    """
    failed = 0
    top = None
    retry_ids = []
    for msg in batch:
        status = await process_inbox_message(main, mem, dest, msg)
        mid = getattr(msg, 'id', 0) or 0
        if top is None or mid > top:
            top = mid
        if status == 'fail':
            failed += 1
        elif status == 'retry':
            retry_ids.append(mid)
    if retry_ids:
        return failed, min(retry_ids) - 1
    return failed, top


async def poll_inbox_first(main, mem):
    """Глубокий первый проход: листаем назад, пока страница полная.

    Штатный первый опрос берёт только свежие INBOX_INITIAL_LIMIT сообщений:
    при 5-часовом зазоре, забитом собственными саммари бота, команда старше
    окна вывалилась бы за границу и была бы пропущена навсегда (last_seen
    встал бы на свежий максимум). Пагинация по offset_id закрывает это.
    Потолок FIRST_SWEEP_MAX_PAGES страниц — от перебора всего канала.
    Возвращает (new_last_seen, failed_count).
    """
    dest = main.RESULTS_DESTINATION
    collected = []
    offset_id = 0
    for _ in range(FIRST_SWEEP_MAX_PAGES):
        page = [m async for m in main.telegram_client.iter_messages(
            dest, limit=INBOX_INITIAL_LIMIT, offset_id=offset_id)]
        page = [m for m in page if getattr(m, 'id', 0)]
        if not page:
            break
        collected.extend(page)
        offset_id = min(m.id for m in page)
        if len(page) < INBOX_INITIAL_LIMIT:
            break
    collected.sort(key=lambda m: m.id)
    if not collected:
        return None, 0
    if len(collected) > INBOX_INITIAL_LIMIT:
        print(f"📥 Inbox: глубокий проход — {len(collected)} сообщений "
              f"(зазор был больше {INBOX_INITIAL_LIMIT})")
    failed, new_last_seen = await _process_batch(main, mem, dest, collected)
    return new_last_seen, failed


async def poll_inbox_once(main, last_seen, mem):
    """Один опрос inbox-канала. Возвращает (new_last_seen, failed_count).

    Итерация отдаёт сообщения ВСЕХ топиков канала (не только General) —
    топик каждой команды определяет process_inbox_message по reply_to.
    last_seen=None → глубокий первый проход poll_inbox_first (см.).
    Дальше — только id > last_seen. last_seen растёт всегда (in-memory).
    """
    dest = main.RESULTS_DESTINATION
    if last_seen is None:
        return await poll_inbox_first(main, mem)
    batch = [m async for m in main.telegram_client.iter_messages(dest, min_id=last_seen)]
    batch.sort(key=lambda m: m.id)

    if not batch:
        return last_seen, 0
    failed, new_last_seen = await _process_batch(main, mem, dest, batch)
    return new_last_seen, failed


async def _watch_cleanup(main, state, path, use_handoff=False):
    """Единая финализация watch: pull-merge, курсор ключей, save, disconnect.

    pull-merge перед save обязателен при handoff: свежая ветка могла уйти
    вперёд (преемник уже пушил), запись обязана быть надмножеством — иначе
    финальный Persist затёр бы чужой прогресс stale-снапшотом. Перезагрузка
    идёт in-place (clear+update), чтобы живая ссылка mem['state'] не протухла.
    """
    if use_handoff:
        pull_state_best_effort(path)
        try:
            fresh = load_state(path)
            state.clear()
            state.update(fresh)
        except Exception as e:
            print(f"⚠️ Не удалось перечитать state: {e}")
    store_key_cursor(main, state)
    try:
        save_state(path, state)
    except Exception as e:
        print(f"⚠️ Не удалось сохранить state: {e}")
    try:
        main.PROGRESS_HOOK = None  # маркер прогресса: дежурство сдано
    except AttributeError:
        pass
    try:
        await main.telegram_client.disconnect()
    except Exception as e:
        print(f"⚠️ Ошибка при disconnect: {e}")
    try:
        await main.http_client.aclose()
    except Exception as e:
        print(f"⚠️ Ошибка при закрытии http_client: {e}")


async def _leader_startup(main, args, state, path, me, poll):
    """Заявить лидерство и подняться. Возвращает 'leader' | 'standby'.

    Claim неготовым → connect → ready + pull предшественника + пауза →
    проверка: флаг уже у более нового (стартовали толпой) — идём в standby,
    а не выходим: толпа сама рассосётся, дежурство не прервётся.
    Самодиагностика пропусков: чужой heartbeat читаем ДО claim'а (свой claim
    его перезапишет); если тишина дольше GAP_WARN_SEC — варнинг в General
    по факту восстановления (слать раньше некому).
    """
    prev_hb, prev_run = prev_duty_heartbeat(me['run_id'])
    print(f"👑 Заявляю лидерство (run {me['run_id']})...")
    leader_claim(me)
    try:
        await main.telegram_client.start(phone=main.PHONE)
    except Exception as e:
        print(f"❌ Не удалось подключиться к Telegram: {e}")
        print("   Проверьте TELEGRAM_SESSION / TELEGRAM_API_ID / TELEGRAM_API_HASH.")
        return 'fail'
    me['ready'] = True
    me['heartbeat_utc'] = _utc_str(_utc_now())
    leader_claim(me)
    note_work_progress()
    try:
        main.PROGRESS_HOOK = note_work_progress  # маркер дёргается между чанками
    except AttributeError:
        pass
    pull_state_best_effort(path)
    try:
        fresh = load_state(path)
        state.clear()
        state.update(fresh)
    except Exception as e:
        print(f"⚠️ Не удалось перечитать state: {e}")
    restore_key_cursor(main, state)
    refresh_schedule_best_effort(main)
    print(f"👑 Дежурство заявлено, пауза {poll}с перед проверкой флага...")
    await asyncio.sleep(poll)
    remote, _ = leader_read()
    if leader_should_yield(me, remote):
        print(f"👑 Пока я стартовал, флаг у {remote.get('run_id')} — ухожу в standby.")
        return 'standby'
    gap_sec, warn = duty_gap_info(prev_hb)
    if gap_sec is not None:
        print(f"👑 Тишина дежурства перед заступлением: {gap_sec // 60} мин "
              f"(последний heartbeat {prev_hb} от {prev_run}).")
        if warn:
            await _notify_inbox(
                main, main.RESULTS_DESTINATION, 1,
                format_gap_warning(gap_sec, prev_hb))
    await liveness_ensure(main)  # маяк дежурства: подхват поиском или новый
    return 'leader'


async def _heartbeat_loop(me, path, main=None):
    """Фоновый heartbeat лидера каждые LEADER_HEARTBEAT_SEC (best-effort).

    Отдельной задачей — осознанно: итерация лидера с тяжёлым саммари длится
    дольше STALE_SEC, heartbeat по счётчику итераций протухал прямо посреди
    задачи и standby объявлял живого мёртвым (наблюдалось в проде 2026-10-04).
    Фоновая задача interleaves на await'ах (Telegram/Gemini HTTP) и бьёт
    ровно по времени. Останавливается отменой от вызывателя.
    Каждый LIVENESS_EVERY-й тик — правка маяка в General (сессия 20).
    Без main (тесты, сольный режим) — только heartbeat, без маяка.
    """
    tick = 0
    try:
        while True:
            await asyncio.sleep(LEADER_HEARTBEAT_SEC)
            me['heartbeat_utc'] = _utc_str(_utc_now())
            leader_claim(me)
            push_state_best_effort(path)
            push_work_best_effort(me['run_id'])  # маркер прогресса едет тем же ритмом
            if main is not None:
                tick += 1
                if tick % LIVENESS_EVERY == 0:
                    await liveness_tick(main)
    except asyncio.CancelledError:
        pass


async def _run_leader_loop(main, args, state, path, me, poll, use_handoff,
                         watch_remaining=None):
    """Дежурный цикл лидера: due + inbox до дедлайна. Возвращает exit-код.

    watch_remaining — остаток вотча при promotion (сессия 12); None — свежий
    лидер берёт полный args.watch_seconds.
    """
    failed_total = 0
    last_seen = None
    inbox_mem = new_inbox_mem()
    inbox_mem['state'] = state  # счётчик попыток живёт в state (переживает handoff)
    sched_every = max(1, SCHEDULE_REFRESH_SEC // poll)
    iteration = 0
    hb_task = None
    if use_handoff:
        hb_task = asyncio.create_task(_heartbeat_loop(me, path, main))
    try:
        deadline = time.monotonic() + leader_watch_seconds(watch_remaining,
                                                             args.watch_seconds)
        try:
            liveness_set_next(_utc_str(
                _utc_now() + timedelta(seconds=max(0.0, deadline - time.monotonic()))))
        except Exception as e:
            print(f"⚠️ Маяк: не удалось посчитать конец вахты: {e}")
        while True:
            iteration += 1
            print(f"─── Итерация {iteration} ───")
            note_work_progress()  # маркер: цикл жив, даже если задач нет
            if use_handoff:
                remote, _ = leader_read()
                if leader_should_yield(me, remote):
                    # Нормально такого нет (новички идут в standby), срабатывает
                    # как разруливатель сплит-брейна: старший молча выходит.
                    print(f"👑 Обнаружен более новый лидер {remote.get('run_id')} — выхожу.")
                    break
                if iteration % sched_every == 0:
                    refresh_schedule_best_effort(main)
            # Inbox всегда первый: команды не ждут догона расписания.
            try:
                last_seen, inbox_failed = await poll_inbox_once(main, last_seen, inbox_mem)
                failed_total += inbox_failed
            except Exception as e:
                print(f"⚠️ Ошибка опроса inbox (итерация {iteration}): {e}")
            failed_total += await run_due_once(main, args, state, path,
                                               max_tasks=MAX_DUE_PER_ITERATION)
            now = time.monotonic()
            if now >= deadline:
                if use_handoff:
                    # Мягкая передача: standby проснётся сразу, а не через stale.
                    me['retiring'] = True
                    me['heartbeat_utc'] = _utc_str(_utc_now())
                    leader_claim(me)
                    push_state_best_effort(path)
                    print("👑 Дедлайн — объявляю retiring, standby принимает дежурство.")
                break
            await asyncio.sleep(min(poll, deadline - now))
    finally:
        if hb_task is not None:
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass
        await _watch_cleanup(main, state, path, use_handoff)

    print(f"🏁 Watch завершён ({iteration} итераций, неуспехов: {failed_total}).")
    return 1 if failed_total else 0


async def _run_standby_loop(main, args, state, path, me, poll):
    """Лёгкий наблюдатель без Telegram: следит за флагом, принимает дежурство.

    Новый standby вытесняет старый по правилу «новее побеждает» (флаг один —
    толпа не копится). Promotion при пропавшем/протухшем (два подряд чтения)
    / retiring лидере; дальше — обычный подъём через _leader_startup.
    Возвращает exit-код (0 — спокойное дежурство/передача).
    """
    me['ready'] = True
    me['heartbeat_utc'] = _utc_str(_utc_now())
    standby_claim(me)
    print(f"🛡️ Standby {me['run_id']}: слежу за лидером, Telegram не подключаю.")
    stale_reads = 0
    wedge_reads = 0
    sb_every = max(1, LEADER_HEARTBEAT_SEC // poll)
    iteration = 0
    try:
        deadline = time.monotonic() + args.watch_seconds
        while True:
            iteration += 1
            now = time.monotonic()
            if now >= deadline:
                print("🛡️ Standby: дедлайн — выхожу, лидер жив и без меня.")
                break
            await asyncio.sleep(min(poll, deadline - time.monotonic()))
            sremote, _ = standby_read()
            if leader_should_yield(me, sremote):
                print(f"🛡️ Более новый standby {sremote.get('run_id')} — выхожу.")
                break
            remote, _ = leader_read()
            takeover_why = None
            if isinstance(remote, dict) and not remote.get('retiring') and leader_is_live(remote):
                stale_reads = 0
                # Лидер дышит, но работа стоит? Маркер прогресса отличает залипшего
                # от занятого длинной задачей (честная работа дёргает маркер между
                # чанками). Нет маркера (старый код) — не свергаем по бездействию.
                try:
                    wdoc, _ = work_read()
                except Exception:
                    wdoc = None
                if work_is_wedged(wdoc, remote):
                    wedge_reads += 1
                    if wedge_reads >= PROMOTE_CONFIRM_READS:
                        takeover_why = (f"завис (heartbeat свеж, а прогресса нет "
                                        f"дольше {WORK_STALE_SEC // 60} мин)")
                else:
                    wedge_reads = 0
            else:
                wedge_reads = 0
                if not isinstance(remote, dict) or remote.get('retiring'):
                    stale_reads = PROMOTE_CONFIRM_READS  # пропал/уходит — сразу
                else:
                    stale_reads += 1
                if standby_should_promote(remote, stale_reads):
                    takeover_why = 'retiring' if isinstance(remote, dict) and remote.get('retiring') \
                        else ('пропал' if not isinstance(remote, dict) else 'мёртв')
            if takeover_why:
                    print(f"🛡️ Лидер {takeover_why} — принимаю дежурство.")
                    res = await _leader_startup(main, args, state, path, me, poll)
                    if res == 'leader':
                        # Наследуем остаток СОБСТВЕННОГО вотча, а не полный заново:
                        # свежий дедлайн пережил бы джоб → килл по таймауту.
                        remaining = max(0.0, deadline - time.monotonic())
                        return await _run_leader_loop(main, args, state, path,
                                                      me, poll, True, remaining)
                    if res == 'standby':
                        stale_reads = 0  # кто-то успел раньше — снова наблюдаем
                        wedge_reads = 0
                        continue
                    return 1  # 'fail' — Telegram не поднялся
            if iteration % sb_every == 0:
                me['heartbeat_utc'] = _utc_str(_utc_now())
                standby_claim(me)
    finally:
        await _watch_cleanup(main, state, path, True)

    return 0


async def run_watch(main, args):
    """Дежурство watch: лидер работает, standby страхует, disconnect один раз.

    Без handoff (нет GITHUB_TOKEN) — сольный режим: сразу лидерский цикл.
    С handoff: при живом лидере новорождённый идёт в standby (Telegram не
    трогает); иначе — подъём лидером. Heartbeat + push state каждые
    LEADER_HEARTBEAT_SEC, refresh SCHEDULE.txt каждые SCHEDULE_REFRESH_SEC.
    """
    path = state_path()
    state = load_state(path)
    restore_key_cursor(main, state)

    use_handoff = handoff_enabled()
    me = new_leader_doc(my_run_id())
    poll = max(1, args.poll_interval)

    if not use_handoff:
        print("👑 Handoff выключен (нет GITHUB_TOKEN/GITHUB_REPOSITORY) — сольный режим.")
        try:
            await main.telegram_client.start(phone=main.PHONE)
        except Exception as e:
            print(f"❌ Не удалось подключиться к Telegram: {e}")
            print("   Проверьте TELEGRAM_SESSION / TELEGRAM_API_ID / TELEGRAM_API_HASH.")
            return 1
        me['ready'] = True
        return await _run_leader_loop(main, args, state, path, me, poll, False)

    remote, _ = leader_read()
    if (isinstance(remote, dict) and remote.get('run_id') != me['run_id']
            and not remote.get('retiring') and leader_is_live(remote)):
        print(f"👑 Лидер {remote.get('run_id')} жив — становлюсь standby.")
        return await _run_standby_loop(main, args, state, path, me, poll)

    res = await _leader_startup(main, args, state, path, me, poll)
    if res == 'leader':
        return await _run_leader_loop(main, args, state, path, me, poll, True)
    if res == 'standby':
        return await _run_standby_loop(main, args, state, path, me, poll)
    # 'fail' — Telegram не поднялся; чистим клиентов и выходим с ошибкой.
    try:
        await main.telegram_client.disconnect()
    except Exception:
        pass
    try:
        await main.http_client.aclose()
    except Exception:
        pass
    return 1


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