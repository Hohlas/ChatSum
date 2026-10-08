"""Модульные тесты чистой логики run_once.py (без сети/Telegram).

Запуск из корня репо:
  ./venv/bin/python tests/test_run_once.py
"""

import asyncio
import os
import re
import sys
from datetime import datetime, timedelta

# Тест живёт в tests/, а main.py/run_once.py — в корне репо.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# main.py на верхнем уровне создаёт TelegramClient, которому нужен
# текущий event loop (Telethon). Обеспечиваем его до импорта.
try:
    asyncio.get_running_loop()
except RuntimeError:
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

import run_once  # noqa: E402
import main as bot  # noqa: E402

FAILURES = []


def check(name, cond, detail=''):
    if cond:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name} {detail}")
        FAILURES.append(name)


def make_entries(now, mins_back):
    t = now - timedelta(minutes=mins_back)
    return [{
        'chat_id': -100,
        'hour': t.hour, 'minute': t.minute,
        'period': '1d', 'post_to_source': False, 'post_as_telegram': False,
    }]


def main():
    now = datetime(2026, 10, 2, 6, 52)

    # Per-key метка: слот due, если нет отметки за дату наступления
    due = run_once.compute_due(make_entries(now, 3), now, {})
    check('per-key: слот без отметки — due', len(due) == 1, due)

    # Отметка за сегодняшнюю дату наступления — skip
    e = make_entries(now, 3)[0]
    key = run_once.task_key(e)
    due = run_once.compute_due([e], now, {key: '2026-10-02'})
    check('per-key: слот с отметкой — skip', len(due) == 0, due)

    # Дыра 5 часов: слот now-300min без отметки — всё равно due (догон)
    due = run_once.compute_due(make_entries(now, 300), now, {})
    check('per-key: слот 5-часовой давности — due', len(due) == 1, due)

    # Свежий last_run при НЕвыполненном слоте — всё равно due (дыра 2 закрыта:
    # глобального окна больше нет, решает только персональная метка)
    due = run_once.compute_due(make_entries(now, 300), now, {'other|00:00|1d': '2026-10-02'})
    check('per-key: чужой прогресс не гасит слот', len(due) == 1, due)

    # Полночь МСК: слот 23:58 пред. суток, now=00:03 — ровно 1 срабатывание
    entries = [{
        'chat_id': -100, 'hour': 23, 'minute': 58,
        'period': '1d', 'post_to_source': False, 'post_as_telegram': False,
    }]
    now_mid = datetime(2026, 10, 2, 0, 3)
    due = run_once.compute_due(entries, now_mid, {})
    check('полночь: ровно 1 срабатывание', len(due) == 1, due)
    if due:
        _, occ, dk = due[0]
        check('полночь: date_key = МСК-дата наступления (пред. сутки)',
              dk == '2026-10-01', f'occ={occ} dk={dk}')
    # Ключи заданий из SCHEDULE.txt
    sched = bot.load_schedule(bot.SCHEDULE_FILE)
    keys = {run_once.task_key(e) for e in sched}
    check('SCHEDULE: ключи уникальны', len(keys) == len(sched), keys)

    # state: roundtrip, prune
    path = '/tmp/opencode/test_state.json'
    if os.path.exists(path):
        os.remove(path)
    run_once.save_state(path, {'last_run_utc': None, 'completed': {'k': '2026-10-02'}})
    s = run_once.load_state(path)
    check('state: roundtrip', s['completed'] == {'k': '2026-10-02'}, s)

    old = (datetime.now(run_once.MSK).date() - timedelta(days=4)).isoformat()
    today = datetime.now(run_once.MSK).date().isoformat()
    run_once.save_state(path, {'last_run_utc': None, 'completed': {'a': old, 'b': today}})
    s2 = run_once.load_state(path)
    run_once.prune_state(s2)
    check('state: prune удаляет старые (3 суток)',
          'a' not in s2['completed'] and s2['completed'].get('b') == today,
          s2['completed'])

    boundary = (datetime.now(run_once.MSK).date() - timedelta(days=3)).isoformat()
    run_once.save_state(path, {'last_run_utc': None, 'completed': {'edge': boundary}})
    s3 = run_once.load_state(path)
    run_once.prune_state(s3)
    check('state: prune сохраняет ровно 3 суток (порог включён)',
          s3['completed'].get('edge') == boundary,
          s3['completed'])

    test_parser()
    test_bundle_keys()
    test_extract_link()
    test_watch_args()
    test_inbox_no_poison()
    test_msg_topic_id()
    test_inbox_v2_flows()
    test_inbox_retry_flow()
    test_sched_flows()
    test_key_cursor()
    test_503_holds_key()
    test_rotate_advances_cursor()
    test_duty_gap()
    test_work_wedge()
    test_leader_watch_inherit()
    test_timeout_budget()
    test_gap_warning_format()
    test_liveness()
    test_due_fail_renew()
    test_quota_diag()
    test_server_overload_diag()
    test_api_error_diag()
    test_model_display_label()
    test_reasoning_effort_config()
    test_flat_rotation()
    test_collect_order_and_parents()
    test_handoff()
    test_duty()
    test_heartbeat_loop()
    test_due_cap()
    test_telegraph_toc()
    test_dedupe_topics()
    test_chunk_rebalance()

    if FAILURES:
        print(f"\n{len(FAILURES)} FAILED: {FAILURES}")
        sys.exit(1)
    print("\nALL TESTS PASSED")


def test_parser():
    p = bot.parse_chat_command_args
    r = p('sum100')
    check('parse sum100: limit', r is not None and r['limit'] == 100 and r['use_ai'] is True, r)
    r = p('sum 100-800')
    check('parse sum 100-800: range', r is not None and (r['range_start'], r['range_end']) == (100, 800), r)
    r = p('sum12h')
    check('parse sum12h: hours', r is not None and r['hours'] == 12, r)
    r = p('sum1d')
    check('parse sum1d: days', r is not None and r['days'] == 1 and r['use_ai'] is True, r)
    r = p('copy1d')
    check('parse copy1d: use_ai=False', r is not None and r['days'] == 1 and r['use_ai'] is False, r)
    r = p('sum1d+')
    check('parse sum1d+: post_to_source', r is not None and r['post_to_source'] is True, r)
    r = p('sum1d-')
    check('parse sum1d-: post_as_telegram', r is not None and r['post_as_telegram'] is True, r)
    r = p('sum')
    check('parse bare sum: default 24h', r is not None and r['hours'] == 24 and r['use_ai'] is True, r)
    r = p('copy')
    check('parse bare copy: default 24h, no AI', r is not None and r['hours'] == 24 and r['use_ai'] is False, r)
    r = p('/sum 2d-3d')
    check('parse /sum 2d-3d: time_range',
          r is not None and str(r['time_range_start']) == '2 days, 0:00:00'
          and str(r['time_range_end']) == '3 days, 0:00:00', r)
    r = p('/sum 3-5d')
    check('parse /sum 3-5d: time_range',
          r is not None and str(r['time_range_start']) == '3 days, 0:00:00'
          and str(r['time_range_end']) == '5 days, 0:00:00', r)
    r = p('/sum 1d 6h')
    check('parse /sum 1d 6h: multi', r is not None and r['days'] == 1 and r['hours'] == 6, r)
    r = p('SUM 100')
    check('parse case-insensitive', r is not None and r['limit'] == 100, r)
    check('parse non-command -> None', p('hello world') is None, p('hello world'))
    check('parse empty -> None', p('   ') is None)
    # Слова с префиксом sum/copy — не команды (граница слова после склейки)
    for txt in ['summary', 'summer', 'copies', 'copycat', '/summary', '/summer job']:
        check(f'parse word {txt!r} -> None', p(txt) is None, p(txt))
    r = p('sum+')
    check('parse sum+: default + post_to_source',
          r is not None and r['hours'] == 24 and r['post_to_source'] is True, r)
    r = p('sum50 t.me/NodesGuru')
    check('parse sum50 + link: limit держится',
          r is not None and r['limit'] == 50 and r['use_ai'] is True, r)
    check('parse sum-up 100 -> None (не команда)', p('sum-up 100') is None, p('sum-up 100'))
    # VPS-эквивалентность: те же строки со слэшем дают те же результаты
    for txt in ['sum100', 'sum 100-800', 'sum12h', 'copy1d', 'sum1d+', 'sum', 'copy 50']:
        a, b = p(txt), p('/' + txt)
        check(f'parse slash-parity {txt!r}', a == b, (a, b))


def test_bundle_keys():
    """Сводный GOOGLE_API_KEYS: любое число ключей, разделители , ; пробел \n,
    порядок primary → indexed(численно) → bundle, дубликаты режутся."""
    saved = dict(os.environ)
    try:
        for k in list(os.environ):
            if k == 'GOOGLE_API_KEY' or k == 'GOOGLE_API_KEYS' \
                    or re.fullmatch(r'GOOGLE_API_KEY\d+', k):
                del os.environ[k]
        os.environ['GOOGLE_API_KEY'] = 'primary'
        os.environ['GOOGLE_API_KEY10'] = 'k10'
        os.environ['GOOGLE_API_KEY2'] = 'k2'
        os.environ['GOOGLE_API_KEYS'] = 'b1,b2\nb3 ; b1'
        keys = bot.load_google_api_keys()
        check('bundle: порядок + дедуп',
              keys == ['primary', 'k2', 'k10', 'b1', 'b2', 'b3'], keys)
        os.environ['GOOGLE_API_KEYS'] = '  ,, \n '
        keys2 = bot.load_google_api_keys()
        check('bundle: пустой bundle игнорируется',
              keys2 == ['primary', 'k2', 'k10'], keys2)
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_extract_link():
    e = run_once.extract_chat_link
    check('link full url', e('sum20 https://t.me/NodesGuru') == ('username', 'NodesGuru'),
          e('sum20 https://t.me/NodesGuru'))
    check('link bare t.me', e('sum t.me/NodesGuru') == ('username', 'NodesGuru'),
          e('sum t.me/NodesGuru'))
    check('link @', e('copy1d @NodesGuru') == ('username', 'NodesGuru'),
          e('copy1d @NodesGuru'))
    check('link c/ with msgid',
          e('sum50 https://t.me/c/1892263845/899001') == ('internal', -1001892263845),
          e('sum50 https://t.me/c/1892263845/899001'))
    check('link c/ with thread',
          e('sum https://t.me/c/1892263845/1?thread=5') == ('internal', -1001892263845),
          e('sum https://t.me/c/1892263845/1?thread=5'))
    check('link joinchat ignored', e('sum https://t.me/joinchat/AAAAbbbb') is None,
          e('sum https://t.me/joinchat/AAAAbbbb'))
    check('link plus ignored', e('sum https://t.me/+AbCdEfGh') is None,
          e('sum https://t.me/+AbCdEfGh'))
    check('link bare word ignored', e('sum hello') is None, e('sum hello'))
    check('link no link', e('sum50') is None, e('sum50'))
    check('link empty', e('') is None, e(''))


def test_watch_args():
    a = run_once.parse_args(['--watch'])
    check('watch defaults', a.watch and a.watch_seconds == 18000 and a.poll_interval == 30, a)
    a = run_once.parse_args(['--watch', '--watch-seconds', '120', '--poll-interval', '10'])
    check('watch custom', a.watch_seconds == 120 and a.poll_interval == 10, a)


def test_inbox_no_poison():
    """Битое 'sum abh' и слово 'summer' не отравляют опрос: валидная
    команда со ссылкой в том же батче выполняется и удаляется."""
    class FakeMsg:
        def __init__(self, id, text):
            self.id = id
            self.text = text
            self.reply_to = None  # General: заголовка ответа нет

    class FakeEntity:
        title = 'SrcChat'

    class FakeClient:
        def __init__(self, msgs):
            self.msgs = msgs
            self.deleted = []

        async def iter_messages(self, dest, limit=None, min_id=None, offset_id=0):
            msgs = self.msgs
            if min_id is not None:
                msgs = [m for m in msgs if m.id > min_id]
            if offset_id:
                msgs = [m for m in msgs if m.id < offset_id]
            if limit is not None:
                msgs = msgs[-int(limit):]
            for m in msgs:
                yield m

        async def get_messages(self, dest, ids=None):
            want = ids if isinstance(ids, (list, tuple)) else [ids]
            found = [m for m in self.msgs if m.id in want]
            if isinstance(ids, (list, tuple)):
                return found
            return found[0] if found else None

        async def get_entity(self, peer_id):
            return FakeEntity()

        async def send_message(self, dest, text, reply_to=None):
            return None

        async def delete_messages(self, dest, ids):
            self.deleted.extend(ids)

    class FakeMain:
        RESULTS_DESTINATION = 'test-inbox'
        parse_chat_command_args = staticmethod(bot.parse_chat_command_args)

        def __init__(self, client):
            self.telegram_client = client
            self.ran = []

        async def get_or_create_topic(self, name):
            return 1

        async def run_analysis(self, **kw):
            self.ran.append(kw)
            return True

    msgs = [
        FakeMsg(1, 'hello'),
        FakeMsg(2, 'sum abh'),
        FakeMsg(3, 'summer'),
        FakeMsg(4, 'sum10 t.me/c/1892263845/50'),
    ]
    client = FakeClient(msgs)
    fake = FakeMain(client)
    new_last_seen, failed = asyncio.get_event_loop().run_until_complete(
        run_once.poll_inbox_once(fake, None, run_once.new_inbox_mem()))
    check('inbox poison: batch consumed', new_last_seen == 4 and failed == 0,
          (new_last_seen, failed))
    check('inbox poison: only valid cmd ran',
          len(fake.ran) == 1 and fake.ran[0]['limit'] == 10 and fake.ran[0]['use_ai'] is True
          and fake.ran[0]['chat_id'] == -1001892263845,
          fake.ran)
    check('inbox poison: only valid cmd deleted', client.deleted == [4], client.deleted)


def _make_inbox_fakes(run_ok=True):
    """Честные фейки inbox (видимость/атрибуты как в проде):

    - iter_messages отдаёт сообщения ВСЕХ топиков (прод: так и есть);
    - заголовок ответа — reply_to (forum_topic/reply_to_msg_id/reply_to_top_id),
      атрибута reply_to_top_id у сообщения НЕТ (как у продового Telethon);
    - GetForumTopics — через вызов клиента;
    - send_message пишет в client.sent для проверки диагностики.
    """
    from types import SimpleNamespace

    class FakeMsg:
        def __init__(self, id, text, top=None, reply_top=None):
            self.id = id
            self.text = text
            # top=7 → сообщение в топике 7; reply_top=7 → ответ НЕ на корень
            # внутри топика 7 (reply_to_msg_id указывает на другое сообщение)
            if top or reply_top:
                self.reply_to = SimpleNamespace(
                    forum_topic=True, reply_to_msg_id=top, reply_to_top_id=reply_top)
            else:
                self.reply_to = None

    class FakeEntity:
        def __init__(self, title):
            self.title = title

    class FakeClient:
        def __init__(self):
            self.msgs = []      # все сообщения канала (iter_messages)
            self.topics = []    # (topic_id, title) — GetForumTopics
            self.deleted = []
            self.sent = []      # (dest, text, reply_to)

        async def __call__(self, request):
            # GetForumTopicsRequest → объект с .topics (id/title)
            return SimpleNamespace(topics=[
                SimpleNamespace(id=tid, title=title)
                for tid, title in self.topics])

        async def iter_messages(self, dest, limit=None, min_id=None, offset_id=0):
            msgs = sorted(self.msgs, key=lambda m: m.id)
            if min_id is not None:
                msgs = [m for m in msgs if m.id > min_id]
            if offset_id:
                msgs = [m for m in msgs if m.id < offset_id]
            if limit is not None:
                msgs = msgs[-int(limit):]
            for m in msgs:
                yield m

        async def get_messages(self, dest, ids=None):
            # Как продовый Telethon: скалярный ids → сообщение или None
            if ids is None:
                return list(self.msgs)
            want = ids if isinstance(ids, (list, tuple)) else [ids]
            found = [m for m in self.msgs if m.id in want]
            if isinstance(ids, (list, tuple)):
                return found
            return found[0] if found else None

        async def get_entity(self, peer):
            return FakeEntity({-1001892263845: 'LinkChat',
                               -100111: 'PairChat'}.get(peer, f'чат {peer}'))

        async def send_message(self, dest, text, reply_to=None):
            self.sent.append((dest, text, reply_to))
            return None

        async def delete_messages(self, dest, ids):
            self.deleted.extend(ids)

    class FakeMain:
        RESULTS_DESTINATION = 'test-inbox'
        parse_chat_command_args = staticmethod(bot.parse_chat_command_args)
        load_schedule = staticmethod(bot.load_schedule)
        save_schedule = staticmethod(bot.save_schedule)
        schedule_period_from_parsed = staticmethod(bot.schedule_period_from_parsed)
        schedule_free_slot = staticmethod(bot.schedule_free_slot)
        _parse_suffixes = staticmethod(bot._parse_suffixes)

        def __init__(self, client, run_ok=True, sched_path=None):
            self.telegram_client = client
            self.ran = []
            self.run_ok = run_ok
            self.SCHEDULE_FILE = sched_path or '/tmp/opencode/test_sched.txt'

        async def get_or_create_topic(self, name):
            return 1

        async def run_analysis(self, **kw):
            self.ran.append(kw)
            return self.run_ok

        async def schedule_listing_text(self):
            return f"LISTING({len(bot.load_schedule(self.SCHEDULE_FILE))})"

    client = FakeClient()
    return FakeMsg, client, FakeMain(client, run_ok=run_ok)


def test_msg_topic_id():
    """msg_topic_id читает заголовок reply_to (у продового Message нет
    атрибута reply_to_top_id — это и убивало топик-команды)."""
    from types import SimpleNamespace as NS

    class M:
        pass

    m = M()
    m.reply_to = None
    check('topic: нет reply_to → None', run_once.msg_topic_id(m) is None)
    m.reply_to = NS(forum_topic=True, reply_to_msg_id=7, reply_to_top_id=None)
    check('topic: корень топика → reply_to_msg_id', run_once.msg_topic_id(m) == 7,
          run_once.msg_topic_id(m))
    m.reply_to = NS(forum_topic=True, reply_to_msg_id=999, reply_to_top_id=7)
    check('topic: ответ не на корень → reply_to_top_id',
          run_once.msg_topic_id(m) == 7, run_once.msg_topic_id(m))
    m.reply_to = NS(forum_topic=False, reply_to_msg_id=7, reply_to_top_id=None)
    check('topic: forum_topic=False → None', run_once.msg_topic_id(m) is None)
    # Честность против прода: у Telethon.Message действительно нет поля
    from telethon.tl.custom.message import Message
    check('topic: у продового Message нет reply_to_top_id',
          not hasattr(Message, 'reply_to_top_id'))


def test_inbox_v2_flows():
    """v2: ссылка в General и sum прямо в топике (включая ответ не на корень)
    выполняются и удаляются; неизвестный топик/вне топика — провал:
    удаление + диагностика с текстом команды."""
    import io
    import contextlib
    FakeMsg, client, fake = _make_inbox_fakes()
    client.msgs = [
        FakeMsg(10, 'sum20 https://t.me/c/1892263845/50'),
        FakeMsg(11, 'sum10', top=7),              # sum прямо в топике 7
        FakeMsg(12, 'sum5', top=9),               # топик есть, чата нет
        FakeMsg(13, 'sum5'),                      # General без ссылки
        FakeMsg(14, 'sum5', top=4033),            # ответ ВНУТРИ General (не топик)
        FakeMsg(15, 'sum5', top=999, reply_top=7),  # ответ не на корень топика 7
        FakeMsg(16, 'sum7 PairChat'),               # General: чат по названию
    ]
    mem = run_once.new_inbox_mem()
    # Предзаполняем кэши (fetch_* — тонкие обёртки Telethon, их гоняет E2E).
    mem['topics'] = {7: 'PairChat', 9: 'Mystery'}
    mem['dialogs'] = {'PairChat': -100111}
    loop = asyncio.get_event_loop()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ls, failed = loop.run_until_complete(run_once.poll_inbox_once(fake, None, mem))
    out = buf.getvalue()
    check('v2: batch consumed', ls == 16 and failed == 3, (ls, failed))
    check('v2: ссылка + 2 топик-команды + имя выполнены', len(fake.ran) == 4, fake.ran)
    by_chat = {kw['chat_id']: kw for kw in fake.ran}
    check('v2: ссылка даёт лимит+чат',
          by_chat.get(-1001892263845, {}).get('limit') == 20, by_chat)
    pair_limits = [kw['limit'] for kw in fake.ran if kw['chat_id'] == -100111]
    check('v2: сум в топике и по названию → чат из диалогов (10, 5, 7)',
          pair_limits == [10, 5, 7], pair_limits)
    check('v2: удалены все командные (успех и провал)',
          client.deleted == [10, 11, 12, 13, 14, 15, 16], client.deleted)
    check('v2: неизвестный топик залогирован',
          out.count('топик 9 не сопоставлен') == 1, out)
    check('v2: вне топика залогировано (13 и 14)',
          out.count('без ссылки') == 2, out)
    # Диагностика: msg12 → топик 9; msg13/14 → General; везде текст команды
    diag12 = [t for (_d, t, r) in client.sent if r == 9 and 'не сопоставлен' in t]
    diag_gen = [t for (_d, t, r) in client.sent
                if r is None and 'не нашёл чат' in t]
    check('v2: диагностика нерезолвленного топика', len(diag12) == 1, client.sent)
    check('v2: диагностика вне топика (13 и 14)', len(diag_gen) == 2, client.sent)
    check('v2: диагностика несёт текст команды',
          diag12 and "Команда: 'sum5'" in diag12[0], diag12)
    check('v2: диагностика говорит про удаление',
          diag12 and 'повтора не будет' in diag12[0], diag12)

    # _log_once: повторный прогон тех же причин — молчит.
    mem2 = run_once.new_inbox_mem()
    mem2['topics'] = {9: 'Mystery'}
    mem2['dialogs'] = {}
    run_once._log_once(mem2, 'topic:9', 'LINE')
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        run_once._log_once(mem2, 'topic:9', 'LINE')
    check('v2: повторный лог подавлен', buf2.getvalue() == '', buf2.getvalue())


def test_inbox_retry_flow():
    """Провал анализа со state: команда живёт до 5 попыток и перечитывается
    уже следующим опросом (last_seen откатывается); успех/исчерпание — удаление."""
    FakeMsg, client, fake = _make_inbox_fakes(run_ok=False)
    client.msgs = [FakeMsg(10, 'sum20 https://t.me/c/1892263845/50')]
    mem = run_once.new_inbox_mem()
    mem['state'] = {}
    loop = asyncio.get_event_loop()
    with _quiet():
        ls1, f1 = loop.run_until_complete(run_once.poll_inbox_once(fake, None, mem))
    check('retry: 1-й провал — не удалена, не failed',
          client.deleted == [] and f1 == 0, (client.deleted, f1))
    check('retry: last_seen откатан ниже команды', ls1 == 9, ls1)
    check('retry: счётчик попыток 1',
          mem['state'].get('inbox_attempts', {}).get('10', {}).get('n') == 1,
          mem['state'].get('inbox_attempts'))
    with _quiet():
        ls2, f2 = loop.run_until_complete(run_once.poll_inbox_once(fake, ls1, mem))
    check('retry: 2-й опрос перечитал команду (не ждём рестарта)',
          len(fake.ran) == 2 and ls2 == 9 and f2 == 0, (len(fake.ran), ls2, f2))
    fake.run_ok = True
    with _quiet():
        ls3, f3 = loop.run_until_complete(run_once.poll_inbox_once(fake, ls2, mem))
    check('retry: успех с 3-й — удалена, счётчик сброшен, last_seen вперёд',
          client.deleted == [10] and mem['state'].get('inbox_attempts') == {}
          and ls3 == 10 and f3 == 0, (client.deleted, ls3, f3))

    # Исчерпание: 5 провалов подряд → удаление + failed
    FakeMsg2, client2, fake2 = _make_inbox_fakes(run_ok=False)
    client2.msgs = [FakeMsg2(20, 'sum20 https://t.me/c/1892263845/50')]
    mem2 = run_once.new_inbox_mem()
    mem2['state'] = {}
    ls = f = None
    with _quiet():
        for _ in range(5):
            ls, f = loop.run_until_complete(run_once.poll_inbox_once(fake2, ls, mem2))
    check('retry: после 5 провалов — удалена и failed',
          client2.deleted == [20] and f == 1 and ls == 20, (client2.deleted, f, ls))


def test_sched_flows():
    """Расписание из General: add (сдвиг занятого слота, дубль, git push),
    sch (публикация), unsch (снятие), битые варианты → провал+удаление."""
    import io
    import contextlib
    FakeMsg, client, fake = _make_inbox_fakes()
    fake.SCHEDULE_FILE = '/tmp/opencode/test_sched_flows.txt'
    pushes = []
    saved_push = run_once.git_push_schedule
    run_once.git_push_schedule = lambda msg: (pushes.append(msg), True)[1]
    loop = asyncio.get_event_loop()
    try:
        def seed(rows):
            bot.save_schedule(fake.SCHEDULE_FILE, rows)

        def row(cid, h, m, period, plus=False):
            return {'chat_id': cid, 'hour': h, 'minute': m, 'period': period,
                    'post_to_source': plus, 'post_as_telegram': False}

        # add: слот свободен → запись + пуш + удаление + подтверждение со списком
        seed([row(-100111, 6, 0, '1d')])
        client.msgs = [FakeMsg(50, '22:33 sum1d t.me/c/1892263845/50')]
        mem = run_once.new_inbox_mem()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ls, failed = loop.run_until_complete(
                run_once.poll_inbox_once(fake, None, mem))
        entries = bot.load_schedule(fake.SCHEDULE_FILE)
        check('sched: запись добавлена',
              len(entries) == 2 and entries[-1]['chat_id'] == -1001892263845
              and entries[-1]['hour'] == 22 and entries[-1]['minute'] == 33
              and entries[-1]['period'] == '1d', entries)
        check('sched: запушен', len(pushes) == 1, pushes)
        check('sched: команда удалена', client.deleted == [50], client.deleted)
        confirm = [t for (_d, t, _r) in client.sent if '✅' in t]
        check('sched: подтверждение + список',
              len(confirm) == 1 and 'LISTING(2)' in confirm[0], client.sent)

        # занятый слот → +5 минут; сумма-limit-период тоже принимается
        client.msgs = [FakeMsg(51, '22:33 sum20 https://t.me/c/1892263845/50')]
        client.sent.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            loop.run_until_complete(run_once.poll_inbox_once(fake, None, mem))
        entries = bot.load_schedule(fake.SCHEDULE_FILE)
        check('sched: занятый слот сдвинут на +5',
              len(entries) == 3 and entries[-1]['minute'] == 38
              and entries[-1]['period'] == '20', entries)
        confirm = [t for (_d, t, _r) in client.sent if '✅' in t]
        check('sched: сдвиг показан в подтверждении',
              confirm and '22:33→22:38' in confirm[0], confirm)

        # точный дубль → без записи, инфо-сообщение
        n_before = len(bot.load_schedule(fake.SCHEDULE_FILE))
        client.msgs = [FakeMsg(52, '22:38 sum20 t.me/c/1892263845/50')]
        client.sent.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            loop.run_until_complete(run_once.poll_inbox_once(fake, None, mem))
        check('sched: дубль не добавлен',
              len(bot.load_schedule(fake.SCHEDULE_FILE)) == n_before
              and any('уже есть' in t for (_d, t, _r) in client.sent),
              client.sent)
        check('sched: дубль удалён без пуша', 52 in client.deleted,
              client.deleted)

        # sch → публикация списка, команда удалена, без пуша
        n_push = len(pushes)
        client.msgs = [FakeMsg(53, 'sch')]
        client.sent.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            loop.run_until_complete(run_once.poll_inbox_once(fake, None, mem))
        check('sch: список опубликован',
              any('LISTING' in t for (_d, t, _r) in client.sent)
              and 53 in client.deleted and len(pushes) == n_push, client.sent)

        # unsch по ссылке → чат убран, пуш
        client.msgs = [FakeMsg(54, 'unsch t.me/c/1892263845/50')]
        client.sent.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            loop.run_until_complete(run_once.poll_inbox_once(fake, None, mem))
        entries = bot.load_schedule(fake.SCHEDULE_FILE)
        check('unsch: чат убран из расписания',
              all(e['chat_id'] != -1001892263845 for e in entries), entries)
        check('unsch: запушен', len(pushes) == n_push + 1, pushes)

        # битые: copy в расписании / без ссылки в General → провал, удалены
        client.msgs = [FakeMsg(55, '22:00 copy1d t.me/c/1892263845/50'),
                       FakeMsg(56, '22:00 sum1d'),
                       FakeMsg(57, '99:00 sum1d t.me/c/1892263845/50')]
        client.sent.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            ls, failed = loop.run_until_complete(
                run_once.poll_inbox_once(fake, None, mem))
        check('sched: битые → fail и удалены',
              failed == 3 and all(i in client.deleted for i in (55, 56, 57)),
              (failed, client.deleted))

        # git push провалился → откат файла + fail
        run_once.git_push_schedule = lambda msg: False
        n_before = len(bot.load_schedule(fake.SCHEDULE_FILE))
        client.msgs = [FakeMsg(58, '07:07 sum1d t.me/c/1892263845/50')]
        client.sent.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            ls, failed = loop.run_until_complete(
                run_once.poll_inbox_once(fake, None, mem))
        check('sched: push-fail → откат и fail',
              failed == 1 and 58 in client.deleted
              and len(bot.load_schedule(fake.SCHEDULE_FILE)) == n_before,
              (failed, client.deleted))
    finally:
        run_once.git_push_schedule = saved_push
        if os.path.exists(fake.SCHEDULE_FILE):
            os.remove(fake.SCHEDULE_FILE)


def test_key_cursor():
    """Курсор ключей: переживает перезапуск через state, мусор → 0."""
    saved = (bot.google_analysis_counter, bot.current_google_key_index,
             list(bot.GOOGLE_API_KEYS), bot.google_client)
    try:
        bot.GOOGLE_API_KEYS = ['a', 'b', 'c']
        bot.google_analysis_counter = 7
        st = {'completed': {}}
        run_once.store_key_cursor(bot, st)
        check('cursor: записан в state',
              st.get(run_once.KEY_CURSOR_STATE_FIELD) == 7, st)
        bot.google_analysis_counter = 0
        run_once.restore_key_cursor(bot, st)
        check('cursor: восстановлен', bot.get_google_key_cursor() == 7,
              bot.get_google_key_cursor())
        # Продолжение ротации: 7 % 3 = 1 → ключ 2/3
        bot.select_google_api_key_for_new_analysis()
        check('cursor: следующий выбор — ключ 2/3',
              bot.current_google_key_index == 1, bot.current_google_key_index)
        # Мусор/отрицательное → 0, не падает
        run_once.restore_key_cursor(bot, {'google_key_cursor': 'xx'})
        check('cursor: мусор → 0', bot.get_google_key_cursor() == 0)
        run_once.restore_key_cursor(bot, {'google_key_cursor': -5})
        check('cursor: отрицательный → 0', bot.get_google_key_cursor() == 0)
        # main без хелперов (старые фейки) — молча пропускаем
        class NoCursor:
            GOOGLE_API_KEYS = ['x']
        run_once.restore_key_cursor(NoCursor(), {'google_key_cursor': 5})
        check('cursor: main без хелперов не падает', True)
    finally:
        (bot.google_analysis_counter, bot.current_google_key_index,
         bot.GOOGLE_API_KEYS, bot.google_client) = saved


def test_503_holds_key():
    """503: один запрос, пауза 3 мин, выход наружу (внутреннего повтора нет,
    поэтому успех в тесте недостижим). Ключ не меняется. 429 — старый путь."""
    saved = (bot.GOOGLE_API_KEYS, bot.current_google_key_index,
             bot.google_analysis_counter, bot.google_client, bot.asyncio.sleep)
    saved_set_index = bot.set_google_api_key_index
    sleeps = []
    calls = {'n': 0}

    async def fake_sleep(sec):
        sleeps.append(sec)

    class FakeCompletions:
        async def create(self, **kw):
            calls['n'] += 1
            raise Exception(
                "Error code: 503 - high demand, status UNAVAILABLE")

    class FakeClient:
        chat = type('C', (), {'completions': FakeCompletions()})()

    def fake_set_index(idx):
        bot.current_google_key_index = idx % len(bot.GOOGLE_API_KEYS)

    loop = asyncio.get_event_loop()
    try:
        bot.GOOGLE_API_KEYS = ['k1', 'k2', 'k3']
        bot.current_google_key_index = 0
        bot.google_analysis_counter = 0
        bot.asyncio.sleep = fake_sleep
        bot.google_client = FakeClient()
        bot.set_google_api_key_index = fake_set_index

        # 503: один запрос, пауза 180, выход наружу — без повтора на ключе
        try:
            with _quiet():
                loop.run_until_complete(bot.execute_gemini_request({}))
            check('503: должен был raise', False)
        except Exception as e:
            check('503: raise после 1 запроса без обхода',
                  '503' in str(e) and bot.current_google_key_index == 0
                  and calls['n'] == 1,
                  (str(e)[:40], bot.current_google_key_index, calls))
        check('503: пауза 3 мин, повтора нет', sleeps == [180], sleeps)
    finally:
        (bot.GOOGLE_API_KEYS, bot.current_google_key_index,
         bot.google_analysis_counter, bot.google_client,
         bot.asyncio.sleep) = saved
        bot.set_google_api_key_index = saved_set_index


def test_rotate_advances_cursor():
    """Связка счётчика с ротацией: следующий анализ стартует со свежего
    ключа, а не возвращается на пропущенные (сессия 8)."""
    saved = (bot.GOOGLE_API_KEYS, bot.current_google_key_index,
             bot.google_analysis_counter, bot.google_client)
    try:
        bot.GOOGLE_API_KEYS = ['k1', 'k2', 'k3']
        bot.google_analysis_counter = 0
        with _quiet():
            bot.select_google_api_key_for_new_analysis()
        check('связка: select стартует с ключа 1/3',
              bot.current_google_key_index == 0, bot.current_google_key_index)
        with _quiet():
            bot.rotate_google_api_key('сухой ключ')
        check('связка: rotate ушёл на ключ 2/3',
              bot.current_google_key_index == 1, bot.current_google_key_index)
        check('связка: rotate двинул счётчик',
              bot.get_google_key_cursor() == 2, bot.get_google_key_cursor())
        with _quiet():
            bot.select_google_api_key_for_new_analysis()
        check('связка: следующий select — ключ 3/3, не назад',
              bot.current_google_key_index == 2, bot.current_google_key_index)
    finally:
        (bot.GOOGLE_API_KEYS, bot.current_google_key_index,
         bot.google_analysis_counter, bot.google_client) = saved


def test_duty_gap():
    """Самодиагностика пропусков: варнинг строго за порогом, мусор молчит."""
    from datetime import timezone
    now = datetime(2026, 10, 4, 18, 0, tzinfo=timezone.utc)
    gap, warn = run_once.duty_gap_info('2026-10-04T17:57:00Z', now)
    check('гэп: 3 мин — тихо', (gap, warn) == (180, False), (gap, warn))
    gap, warn = run_once.duty_gap_info('2026-10-04T17:52:00Z', now)
    check('гэп: 8 мин — варнинг', (gap, warn) == (480, True), (gap, warn))
    gap, warn = run_once.duty_gap_info('2026-10-04T17:56:00Z', now)
    check('гэп: ровно порог — тихо (строго больше)',
          (gap, warn) == (240, False), (gap, warn))
    for bad in (None, 'xx', '2026-10-04T18:05:00Z'):
        gap, warn = run_once.duty_gap_info(bad, now)
        check(f'гэп: {bad} — молчим', (gap, warn) == (None, False), (gap, warn))

    saved = (run_once.leader_read, run_once.standby_read)
    try:
        run_once.leader_read = lambda: (
            {'run_id': 'old/1', 'heartbeat_utc': '2026-10-04T17:50:00Z'}, 'a')
        run_once.standby_read = lambda: (
            {'run_id': 'me/1', 'heartbeat_utc': '2026-10-04T17:59:00Z'}, 'b')
        hb, run = run_once.prev_duty_heartbeat('me/1')
        check('гэп: свой свежий standby дыру не маскирует',
              (hb, run) == ('2026-10-04T17:50:00Z', 'old/1'), (hb, run))
        run_once.leader_read = lambda: (None, None)
        hb, run = run_once.prev_duty_heartbeat('me/1')
        check('гэп: остался только свой — базы нет',
              (hb, run) == (None, None), (hb, run))
    finally:
        run_once.leader_read, run_once.standby_read = saved


def test_work_wedge():
    """Маркер прогресса: свергаем только зависшего текущего лидера."""
    from datetime import timezone
    now = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)
    leader = {'run_id': 'old/1', 'heartbeat_utc': '2026-10-05T07:59:00Z'}
    fresh = {'run_id': 'old/1', 'last_work_utc': '2026-10-05T07:59:00Z'}
    check('маркер: свежий прогресс — не завис',
          run_once.work_is_wedged(fresh, leader, now) is False)
    stale = {'run_id': 'old/1', 'last_work_utc': '2026-10-05T07:00:00Z'}
    check('маркер: час без прогресса — завис',
          run_once.work_is_wedged(stale, leader, now) is True)
    edge = {'run_id': 'old/1', 'last_work_utc': '2026-10-05T07:45:00Z'}
    check('маркер: ровно порог (15 мин) — ещё не завис (строго больше)',
          run_once.work_is_wedged(edge, leader, now) is False)
    slow = {'run_id': 'old/1', 'last_work_utc': '2026-10-05T07:44:00Z'}
    check('маркер: 16 мин — завис (худший честный чанк ~12 мин уже позади)',
          run_once.work_is_wedged(slow, leader, now) is True)
    чужой = {'run_id': 'older/9', 'last_work_utc': '2026-10-05T07:00:00Z'}
    check('маркер: протухший чужой run_id — не трогаем',
          run_once.work_is_wedged(чужой, leader, now) is False)
    for bad in (None, 'xx', {'run_id': 'old/1'}):
        check(f'маркер: {bad} — по отсутствию данных не свергаем',
              run_once.work_is_wedged(bad, leader, now) is False)
    check('маркер: нет лидера — не свергаем',
          run_once.work_is_wedged(stale, None, now) is False)
    future = {'run_id': 'old/1', 'last_work_utc': '2026-10-05T08:05:00Z'}
    check('маркер: будущее — не завис',
          run_once.work_is_wedged(future, leader, now) is False)
    run_once._last_work_utc = None
    run_once.note_work_progress()
    ts = run_once._parse_utc(run_once._last_work_utc)
    check('маркер: note ставит свежую метку', ts is not None)
    run_once._last_work_utc = None


def test_leader_watch_inherit():
    """Наследование дедлайна: свежий лидер — полный вотч, promoted — остаток."""
    check('наследование: свежий лидер берёт полный вотч',
          run_once.leader_watch_seconds(None, 19800) == 19800)
    check('наследование: promotion берёт остаток',
          run_once.leader_watch_seconds(10380.5, 19800) == 10380.5)
    check('наследование: нулевой остаток — сразу retiring',
          run_once.leader_watch_seconds(0, 19800) == 0)
    check('наследование: отрицательный остаток — в ноль, не в минус',
          run_once.leader_watch_seconds(-42, 19800) == 0)


def test_timeout_budget():
    """Бюджет джоба: вотч + хвост + установка ≤ таймаут (урок ночи 05.10)."""
    yml = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       '.github', 'workflows', 'summarize.yml')
    text = open(yml, encoding='utf-8').read()
    timeout = int(re.search(r'timeout-minutes:\s*(\d+)', text).group(1))
    watch = int(re.search(r'WATCH_SECONDS="\$\{WATCH_SECONDS:-(\d+)\}"', text).group(1))
    tail, setup = 900, 60  # допущение «хвост ≤15 мин» + замер установки ~47с
    check('бюджет: вотч+хвост+установка влезают в таймаут',
          (watch + tail + setup) <= timeout * 60, (watch, timeout))
    check('бюджет: запас не меньше 5 мин',
          timeout * 60 - (watch + tail + setup) >= 300, timeout)


def test_gap_warning_format():
    """Формат варнинга: две строки, время МСК (UTC+3)."""
    text = run_once.format_gap_warning(720, '2026-10-05T05:52:27Z')
    check('варнинг: короткий формат с МСК',
          text == '⚠️ Дежурство прерывалось на 12 мин.\n'
                  'Последний heartbeat 2026-10-05 08:52:27', repr(text))
    text = run_once.format_gap_warning(300, 'мусор')
    check('варнинг: мусор не роняет, строка как есть',
          text == '⚠️ Дежурство прерывалось на 5 мин.\n'
                  'Последний heartbeat мусор', repr(text))


def test_liveness():
    """Маяк дежурства: формат МСК, подхват поиском, правка, перепост при сносе."""
    from datetime import timezone
    now = datetime(2026, 10, 5, 9, 46, tzinfo=timezone.utc)  # 12:46 МСК
    check('маяк: одна строка без конца вахты',
          run_once.format_liveness_text(now) == 'heartbeat 05.10 12:46',
          repr(run_once.format_liveness_text(now)))
    check('маяк: две строки с next leader (всё МСК)',
          run_once.format_liveness_text(now, '2026-10-05T12:13:00Z')
          == 'heartbeat 05.10 12:46\nnext leader: 05.10 15:13',
          repr(run_once.format_liveness_text(now, '2026-10-05T12:13:00Z')))
    check('маяк: префикс свой/чужой',
          run_once.is_liveness_text('heartbeat 05.10 12:46\nnext leader: 05.10 15:13') is True
          and run_once.is_liveness_text('hello') is False
          and run_once.is_liveness_text(None) is False)

    class FakeMsg:
        def __init__(self, id, text):
            self.id = id
            self.text = text

    class FakeClient:
        def __init__(self, msgs):
            self.msgs = list(msgs)
            self.sent = []
            self.edited = []
            self.fail_edit = False
            self._next = 100

        async def iter_messages(self, dest, limit=None):
            msgs = self.msgs[-int(limit):] if limit else self.msgs
            for m in msgs:
                yield m

        async def send_message(self, dest, text):
            self._next += 1
            m = FakeMsg(self._next, text)
            self.msgs.append(m)
            self.sent.append(text)
            return m

        async def edit_message(self, dest, msg_id, text):
            if self.fail_edit:
                raise RuntimeError('deleted')
            self.edited.append((msg_id, text))
            for m in self.msgs:
                if m.id == msg_id:
                    m.text = text

    class FakeMain:
        RESULTS_DESTINATION = 'test-general'

        def __init__(self, client):
            self.telegram_client = client

    loop = asyncio.get_event_loop()
    try:
        # Подхват: максимальный id среди маяков, чужое игнорируем, поста нет
        c1 = FakeClient([FakeMsg(1, 'hello'),
                         FakeMsg(7, 'heartbeat 05.10 09:00 (по москве)'),
                         FakeMsg(9, '📄 саммари'),
                         FakeMsg(12, 'heartbeat 05.10 10:00 (по москве)')])
        run_once._liveness_msg_id = None
        got = loop.run_until_complete(run_once.liveness_ensure(FakeMain(c1)))
        check('маяк: подхват последнего маяка без поста',
              got == 12 and c1.sent == [], (got, c1.sent))

        # Нечего подхватить — постим новый
        c2 = FakeClient([FakeMsg(1, 'hello')])
        run_once._liveness_msg_id = None
        got = loop.run_until_complete(run_once.liveness_ensure(FakeMain(c2)))
        check('маяк: без истории — пост нового',
              got == 101 and len(c2.sent) == 1, (got, c2.sent))

        # Тик правит in-memory id
        run_once._liveness_msg_id = 12
        run_once._liveness_next_utc = None
        got = loop.run_until_complete(run_once.liveness_tick(FakeMain(c1)))
        check('маяк: тик правит in-memory id',
              got == 12 and c1.edited and c1.edited[-1][0] == 12,
              (got, c1.edited))

        # Тик несёт строку next leader, если конец вахты известен
        run_once._liveness_msg_id = 12
        run_once._liveness_next_utc = '2026-10-05T12:13:00Z'
        got = loop.run_until_complete(run_once.liveness_tick(FakeMain(c1)))
        check('маяк: тик пишет next leader',
              got == 12 and c1.edited and c1.edited[-1][1].endswith(
                  '\nnext leader: 05.10 15:13'),
              (got, c1.edited[-1] if c1.edited else None))
        run_once._liveness_next_utc = None

        # Правка упала (удалён вручную) — перепост + новый id запомнен
        c1.fail_edit = True
        run_once._liveness_msg_id = 12
        got = loop.run_until_complete(run_once.liveness_tick(FakeMain(c1)))
        check('маяк: снос лечится перепостом',
              got == 101 and len(c1.sent) == 1, (got, c1.sent))
        check('маяк: новый id запомнен', run_once._liveness_msg_id == 101,
              run_once._liveness_msg_id)

        # Ритм: heartbeat-loop правит маяк каждый 5-й тик
        saved = (run_once.LEADER_HEARTBEAT_SEC, run_once.leader_claim,
                 run_once.push_state_best_effort, run_once.work_claim)
        try:
            run_once.LEADER_HEARTBEAT_SEC = 0.05
            run_once.leader_claim = lambda doc: True
            run_once.push_state_best_effort = lambda path: True
            run_once.work_claim = lambda doc: True
            c3 = FakeClient([FakeMsg(5, 'heartbeat 05.10 08:00 (по москве)')])
            run_once._liveness_msg_id = 5
            fm = FakeMain(c3)

            async def run_ticks():
                me = {'run_id': 't', 'ready': True, 'heartbeat_utc': 'old',
                      'watch_started_utc': '2026-10-05T09:00:00Z'}
                task = asyncio.create_task(
                    run_once._heartbeat_loop(me, '/tmp/x.json', fm))
                await asyncio.sleep(0.4)  # ~8 тиков → правка на 5-м
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            loop.run_until_complete(run_ticks())
            check('маяк: heartbeat-loop правит каждый 5-й тик',
                  len(c3.edited) >= 1 and c3.edited[0][0] == 5, c3.edited)
        finally:
            (run_once.LEADER_HEARTBEAT_SEC, run_once.leader_claim,
             run_once.push_state_best_effort, run_once.work_claim) = saved
    finally:
        run_once._liveness_msg_id = None
        run_once._liveness_next_utc = None


def test_due_fail_renew():
    """Возобновляемый скип: окно истекло — новый эпизод, иначе серия растёт."""
    from datetime import timezone, timedelta
    now = datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc)
    iso = lambda dt: dt.strftime('%Y-%m-%dT%H:%M:%SZ')

    r = run_once.due_fail_record({}, 'k', now)
    check('скип: первый провал — n=1, метки свежие',
          r == {'n': 1, 'first_utc': iso(now), 'last_utc': iso(now)}, r)

    r2 = run_once.due_fail_record({'k': r}, 'k', now + timedelta(minutes=20))
    check('скип: повтор в окне — n=2, first kept, last движется',
          r2['n'] == 2 and r2['first_utc'] == iso(now)
          and r2['last_utc'] == iso(now + timedelta(minutes=20)), r2)
    check('скип: 2 провала — работаем',
          run_once.due_skip_info({'k': r2}, 'k', now + timedelta(minutes=20)) is False)
    r3 = run_once.due_fail_record({'k': r2}, 'k', now + timedelta(minutes=40))
    check('скип: 3 свежих провала — пропуск',
          run_once.due_skip_info({'k': r3}, 'k', now + timedelta(minutes=40)) is True)

    r4 = run_once.due_fail_record({'k': r3}, 'k', now + timedelta(hours=2))
    check('скип: окно истекло — новый эпизод n=1',
          r4 == {'n': 1, 'first_utc': iso(now + timedelta(hours=2)),
                 'last_utc': iso(now + timedelta(hours=2))}, r4)
    check('скип: новый эпизод — работаем',
          run_once.due_skip_info({'k': r4}, 'k', now + timedelta(hours=2)) is False)

    stale = {'k': {'n': 46, 'first_utc': '2026-10-05T17:12:34Z'}}
    check('скип: stale без last_utc — не скипаем (fallback first_utc)',
          run_once.due_skip_info(stale, 'k', now) is False)
    r5 = run_once.due_fail_record(stale, 'k', now)
    check('скип: stale самозалечивается новым эпизодом', r5['n'] == 1, r5)

    r6 = run_once.due_fail_record({'k': {'n': 'xx'}}, 'k', now)
    check('скип: мусор — новый эпизод', r6['n'] == 1, r6)


def test_quota_diag():
    """Диагностика 429: видно измерение квоты и время сброса; мусор — ''."""
    from types import SimpleNamespace

    def err(payload):
        return SimpleNamespace(
            status_code=429,
            response=SimpleNamespace(json=lambda: payload))

    daily = {'error': {'message': 'quota',
                       'details': [{'@type': 'x/QuotaFailure',
                                    'violations': [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]},
                                   {'@type': 'x/RetryInfo', 'retryDelay': '24445s'}]}}
    check('диагностика: дневная квота + сброс',
          bot.format_quota_diagnostic(err(daily))
          == 'HTTP 429 · GenerateRequestsPerDayPerProjectPerModel · retry 6h47m',
          bot.format_quota_diagnostic(err(daily)))
    per_min = {'error': {'details': [{'@type': 'x/QuotaFailure',
                                      'violations': [{'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier'}]},
                                     {'@type': 'x/RetryInfo', 'retryDelay': '25s'}]}}
    check('диагностика: поминутная квота отличима',
          bot.format_quota_diagnostic(err(per_min))
          == 'HTTP 429 · GenerateRequestsPerMinutePerProjectPerModel · retry 25s',
          bot.format_quota_diagnostic(err(per_min)))
    check('диагностика: retry 90с → минуты',
          bot._short_retry_delay('90s') == '1m', bot._short_retry_delay('90s'))
    for bad in (None, 'xx', {}, {'error': {}}, {'error': {'details': 'xx'}}):
        check(f'диагностика: мусор {bad} → пусто',
              bot.format_quota_diagnostic(err(bad)) == '',
              repr(bot.format_quota_diagnostic(err(bad))))
    check('диагностика: нет response → пусто',
          bot.format_quota_diagnostic(SimpleNamespace(status_code=429)) == '')


def test_server_overload_diag():
    """Диагностика 503: в TG — дословный текст Google; заголовки — в лог."""
    from types import SimpleNamespace
    import contextlib
    import io

    demand = ('This model is currently experiencing high demand. '
              'Spikes in demand are usually temporary. Please try again later.')

    def err(message):
        return SimpleNamespace(
            status_code=503,
            response=SimpleNamespace(
                json=lambda: {'error': {'message': message}},
                headers={'retry-after': '5', 'x-request-id': 'abc'}))

    check('503-диагностика: дословный текст Google',
          bot.format_server_overload_diagnostic(err(demand)) == demand,
          bot.format_server_overload_diagnostic(err(demand)))
    check('503-диагностика: пустое сообщение → пусто',
          bot.format_server_overload_diagnostic(err('')) == '',
          repr(bot.format_server_overload_diagnostic(err(''))))
    check('503-диагностика: нет response → пусто (без исключения)',
          bot.format_server_overload_diagnostic(
              SimpleNamespace(status_code=503)) == '',
          repr(bot.format_server_overload_diagnostic(
              SimpleNamespace(status_code=503))))
    check('503-диагностика: 429 не выдаёт себя за перегрузку',
          bot.is_server_overloaded(
              'Error code: 429 - quota exceeded') is False, '')

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        bot._log_response_headers(err(demand))
    out = buf.getvalue()
    check('503-лог: заголовки пишутся в stdout',
          'retry-after=5' in out and 'x-request-id=abc' in out, repr(out))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        bot._log_response_headers(SimpleNamespace(status_code=503))
    check('503-лог: нет заголовков — строка, без исключения',
          'недоступны' in buf.getvalue(), repr(buf.getvalue()))


def test_model_display_label():
    """Подпись модели: effort показывается всегда, если задан (включая none)."""
    check('подпись: модель + effort',
          bot.model_display_label('gemini-3.8-flash', 'medium') == 'gemini-3.8-flash medium',
          bot.model_display_label('gemini-3.8-flash', 'medium'))
    check('подпись: effort none → показывается явно',
          bot.model_display_label('gemini-3.8-flash', 'none') == 'gemini-3.8-flash none',
          bot.model_display_label('gemini-3.8-flash', 'none'))
    check('подпись: effort пусто → только модель',
          bot.model_display_label('gemini-3.8-flash', '') == 'gemini-3.8-flash',
          bot.model_display_label('gemini-3.8-flash', ''))
    check('подпись: effort регистр/пробелы нормализуются',
          bot.model_display_label('gemini-3.8-flash', ' HIGH ') == 'gemini-3.8-flash high',
          bot.model_display_label('gemini-3.8-flash', ' HIGH '))
    check('подпись: без модели — не падает',
          bot.model_display_label('', 'medium') == 'medium',
          repr(bot.model_display_label('', 'medium')))


def test_reasoning_effort_config():
    """'none' уходит в запрос явно; пусто — параметр не шлём (дефолт модели)."""
    saved = bot.GEMINI_REASONING_EFFORT
    try:
        for effort in ('none', 'low', 'medium', 'high'):
            bot.GEMINI_REASONING_EFFORT = effort
            cfg = bot.get_model_generation_config('gemini-3.6-flash')
            check(f'effort: {effort} передаётся как есть',
                  cfg['reasoning_effort'] == effort, cfg['reasoning_effort'])
        bot.GEMINI_REASONING_EFFORT = ''
        cfg = bot.get_model_generation_config('gemini-3.6-flash')
        check('effort: пусто → None (параметр не шлём)',
              cfg['reasoning_effort'] is None, cfg['reasoning_effort'])
    finally:
        bot.GEMINI_REASONING_EFFORT = saved


def test_api_error_diag():
    """Диагностика LLM-ошибок: дословный текст Google для любого статуса."""
    from types import SimpleNamespace
    import contextlib
    import io

    def err(status, message, text=None):
        resp = SimpleNamespace(
            json=lambda: {'error': {'message': message}},
            headers={'x-request-id': f'r{status}'})
        if text is not None:
            resp.text = text
        return SimpleNamespace(status_code=status, response=resp)

    check('диагностика API: 400 отдаёт текст Google',
          bot.format_api_error_diagnostic(err(400, 'API key not valid.'))
          == 'API key not valid.',
          bot.format_api_error_diagnostic(err(400, 'API key not valid.')))
    check('диагностика API: 500 отдаёт текст Google',
          bot.format_api_error_diagnostic(err(500, 'Internal error')) == 'Internal error',
          bot.format_api_error_diagnostic(err(500, 'Internal error')))
    check('диагностика API: пустое сообщение → пусто',
          bot.format_api_error_diagnostic(err(403, '')) == '',
          repr(bot.format_api_error_diagnostic(err(403, ''))))
    check('диагностика API: нет response → пусто',
          bot.format_api_error_diagnostic(SimpleNamespace(status_code=500)) == '',
          repr(bot.format_api_error_diagnostic(SimpleNamespace(status_code=500))))
    check('диагностика API: 503 тег — алиас',
          bot.format_server_overload_diagnostic(err(503, 'overloaded')) == 'overloaded',
          bot.format_server_overload_diagnostic(err(503, 'overloaded')))

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        bot.format_api_error_diagnostic(err(500, 'boom'), 'HTTP 500')
    check('диагностика API: заголовки дампятся в лог по тегу',
          'HTTP 500' in buf.getvalue() and 'x-request-id=r500' in buf.getvalue(),
          repr(buf.getvalue()))


def test_flat_rotation():
    """Плоская ротация: квота → сразу дальше; retry-время из текста message."""
    import httpx
    from openai import APIStatusError

    def _resp(status, payload):
        req = httpx.Request('POST', 'https://generativelanguage.googleapis.com/x')
        return httpx.Response(status, json=payload, request=req)

    msg = ('You exceeded your current quota, please check your plan and billing details. '
           '* Quota exceeded for metric: generativelanguage.googleapis.com/'
           'generate_content_free_tier_requests, limit: 20, model: gemini-3.6-flash'
           '\nPlease retry in 17h1m40.176678811s.')
    payload = {'error': {'code': 429, 'message': msg, 'status': 'RESOURCE_EXHAUSTED',
               'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': []},
                           {'@type': 'type.googleapis.com/google.rpc.QuotaFailure',
                            'violations': [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]}]}}
    quota_err = APIStatusError(message='429', response=_resp(429, payload), body=payload)
    check('плоская: 429-квота опознана',
          bot.is_quota_rate_limit(quota_err) is True, '')
    check('плоская: retry из текста трейса → TG',
          bot.format_quota_diagnostic(quota_err)
          == 'HTTP 429 · GenerateRequestsPerDayPerProjectPerModel · retry 17h01m',
          bot.format_quota_diagnostic(quota_err))
    storm = APIStatusError(message='503',
                           response=_resp(503, {'error': {'message': 'Service Unavailable'}}),
                           body={})
    check('плоская: 503 — не квота (идёт путём с повторами)',
          bot.is_quota_rate_limit(storm) is False, '')
    check('плоская: обычное исключение — не квота',
          bot.is_quota_rate_limit(ValueError('x')) is False, '')
    check('плоская: пауза проигранного круга 60с',
          bot.FULL_CIRCLE_FAIL_PAUSE_SEC == 60, bot.FULL_CIRCLE_FAIL_PAUSE_SEC)
    for raw, want in (('17h1m40.176678811s', '17h01m'), ('1m30s', '1m'),
                      ('45s', '45s'), ('24445s', '6h47m'), ('xx', 'xx')):
        check(f'плоская: retry {raw} → {want}',
              bot._short_retry_delay(raw) == want, bot._short_retry_delay(raw))
    check('503: пауза 3 мин',
          bot.SERVER_OVERLOAD_PAUSE_SEC == 180, bot.SERVER_OVERLOAD_PAUSE_SEC)
    for text in ('Error code: 503 - Service Unavailable',
                 'UNAVAILABLE: server overloaded',
                 'HTTP 503: upstream overloaded'):
        check(f'503: держим ключ ({text[:24]}…)',
              bot.is_server_overloaded(text) is True, '')
    for text in ('Error code: 429 - quota exceeded',
                 'GenerateRequestsPerDayPerProjectPerModel-FreeTier',
                 'timeout awaiting response', 'Error code: 500',
                 'Error code: 502', 'Error code: 504', '', None):
        check(f'503: чужое ({str(text)[:24]}) — не держим',
              bot.is_server_overloaded(text) is False, '')


def test_collect_order_and_parents():
    """К1/К2: ключ сортировки (date, id); родители по r не вылетают как шум."""
    msgs = [
        {'sender': 'B', 'text': 'второе', 'date': '2026-10-06 10:00:00',
         'message_id': 5, 'reply_to': None},
        {'sender': 'A', 'text': 'первое', 'date': '2026-10-06 10:00:00',
         'message_id': 3, 'reply_to': None},
        {'sender': 'C', 'text': 'раннее', 'date': '2026-10-06 09:59:59',
         'message_id': 7, 'reply_to': None},
    ]
    ordered = sorted(msgs, key=bot._message_sort_key)
    check('сортировка: (date, id)',
          [m['message_id'] for m in ordered] == [7, 3, 5],
          [m['message_id'] for m in ordered])
    # Родитель-шум, на который есть ссылка, сохраняется как контекст
    noisy = [
        {'sender': 'A', 'text': 'ok', 'date': '2026-10-06 10:00:00',
         'message_id': 10, 'reply_to': None},
        {'sender': 'B', 'text': 'развёрнутый ответ по существу вопроса', 'date': '2026-10-06 10:00:01',
         'message_id': 11, 'reply_to': 10},
        {'sender': 'C', 'text': 'ok', 'date': '2026-10-06 10:00:02',
         'message_id': 12, 'reply_to': None},
    ]
    with _quiet():
        kept = bot.optimize_messages([dict(m) for m in noisy], '193')
    kept_ids = sorted(m['message_id'] for m in kept)
    check('родитель по r не вылетает как шум', 10 in kept_ids, kept_ids)
    check('несвязанный шум вылетает', 12 not in kept_ids, kept_ids)
    # Родитель из списка исключённых тоже сохраняется как контекст
    saved = bot.EXCLUDED_USERS
    bot.EXCLUDED_USERS = ['Spammer']
    try:
        excl = [
            {'sender': 'Spammer', 'text': 'длинный пост с разбором по существу темы',
             'date': '2026-10-06 10:00:00', 'message_id': 20, 'reply_to': None},
            {'sender': 'B', 'text': 'возражение с аргументами по пунктам',
             'date': '2026-10-06 10:00:01', 'message_id': 21, 'reply_to': 20},
        ]
        with _quiet():
            kept2 = bot.optimize_messages([dict(m) for m in excl], '193')
        check('родитель-исключённый не вылетает',
              any(m['message_id'] == 20 for m in kept2),
              [m['message_id'] for m in kept2])
    finally:
        bot.EXCLUDED_USERS = saved


def test_handoff():
    """Лидерство: уступаем только живому готовому более новому флагу."""
    from datetime import timezone
    now = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)

    def doc(run, ready, hb_sec_ago, started, retiring=False):
        hb = (now - timedelta(seconds=hb_sec_ago)).strftime('%Y-%m-%dT%H:%M:%SZ')
        d = {'run_id': run, 'ready': ready,
             'heartbeat_utc': hb, 'watch_started_utc': started}
        if retiring:
            d['retiring'] = True
        return d

    mine = doc('111/1', True, 0, '2026-10-04T09:00:00Z')

    # Свой run_id — не уступаем
    check('handoff: свой флаг — не уступаем',
          run_once.leader_should_yield(mine, dict(mine), now) is False)
    # Нет флага — не уступаем
    check('handoff: нет флага — не уступаем',
          run_once.leader_should_yield(mine, None, now) is False)
    # Мёртвый лидер (heartbeat 30 мин при stale 300с) — не уступаем
    dead = doc('222/1', True, 1800, '2026-10-04T09:30:00Z')
    check('handoff: мёртвый лидер — не уступаем',
          run_once.leader_should_yield(mine, dead, now) is False)
    check('handoff: мёртвый — не live', run_once.leader_is_live(dead, now) is False)
    # Граница stale: 299с — жив, 301с — мёртв
    edge_live = doc('x', True, 299, '2026-10-04T09:30:00Z')
    edge_dead = doc('x', True, 301, '2026-10-04T09:30:00Z')
    check('handoff: 299с — live', run_once.leader_is_live(edge_live, now) is True)
    check('handoff: 301с — не live', run_once.leader_is_live(edge_dead, now) is False)
    # Стартущий (ready=false), но новее — НЕ уступаем, он ещё не дежурит
    starting = doc('333/1', False, 0, '2026-10-04T09:30:00Z')
    check('handoff: стартущий — не уступаем',
          run_once.leader_should_yield(mine, starting, now) is False)
    check('handoff: стартущий — live', run_once.leader_is_live(starting, now) is True)
    # Готовый новее — уступаем (передача флага)
    newer = doc('444/1', True, 0, '2026-10-04T09:30:00Z')
    check('handoff: готовый новее — уступаем',
          run_once.leader_should_yield(mine, newer, now) is True)
    # Готовый старше — не уступаем (мы новее, флаг наш)
    older = doc('000/1', True, 0, '2026-10-04T08:00:00Z')
    check('handoff: готовый старше — не уступаем',
          run_once.leader_should_yield(mine, older, now) is False)
    # Равный старт в одну секунду: побеждает больший run_id (оба не уступают —
    # дыры «остались без лидера» нет)
    tie_a = doc('aaa', True, 0, '2026-10-04T09:30:00Z')
    tie_b = doc('zzz', True, 0, '2026-10-04T09:30:00Z')
    check('handoff: tie — меньший уступает',
          run_once.leader_should_yield(tie_a, tie_b, now) is True)
    check('handoff: tie — больший остаётся',
          run_once.leader_should_yield(tie_b, tie_a, now) is False)
    # Битый heartbeat — не live, не уступаем
    broken = {'run_id': 'x', 'ready': True, 'heartbeat_utc': 'мусор',
              'watch_started_utc': '2026-10-04T09:30:00Z'}
    check('handoff: битый heartbeat — не уступаем',
          run_once.leader_should_yield(mine, broken, now) is False)

    # merge_completed: объединение, побеждает свежая дата
    merged = run_once.merge_completed({'a': '2026-10-04', 'b': '2026-10-03'},
                                      {'b': '2026-10-04', 'c': '2026-10-04'})
    check('handoff: merge union + свежая побеждает',
          merged == {'a': '2026-10-04', 'b': '2026-10-04', 'c': '2026-10-04'}, merged)

    # my_run_id: формат GitHub и локальный фолбэк (без сети)
    saved = (os.getenv('GITHUB_RUN_ID'), os.getenv('GITHUB_RUN_ATTEMPT'))
    try:
        os.environ['GITHUB_RUN_ID'] = '123'
        os.environ['GITHUB_RUN_ATTEMPT'] = '2'
        check('handoff: run_id из env', run_once.my_run_id() == '123/2', run_once.my_run_id())
        del os.environ['GITHUB_RUN_ID']
        os.environ.pop('GITHUB_RUN_ATTEMPT', None)
        rid = run_once.my_run_id()
        check('handoff: локальный фолбэк', rid.startswith('local-'), rid)
        # Без токена — соло, сеть не трогаем
        os.environ.pop('GITHUB_TOKEN', None)
        check('handoff: без токена выключен', run_once.handoff_enabled() is False)
    finally:
        for k, v in (('GITHUB_RUN_ID', saved[0]), ('GITHUB_RUN_ATTEMPT', saved[1])):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_duty():
    """Standby/promote, due-backoff, inbox-попытки, merge state, prune счётчиков."""
    from datetime import timezone
    now = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)

    def doc(run, ready, hb_sec_ago, started, retiring=False):
        hb = (now - timedelta(seconds=hb_sec_ago)).strftime('%Y-%m-%dT%H:%M:%SZ')
        d = {'run_id': run, 'ready': ready,
             'heartbeat_utc': hb, 'watch_started_utc': started}
        if retiring:
            d['retiring'] = True
        return d

    live = doc('111/1', True, 10, '2026-10-04T09:00:00Z')
    stale = doc('111/1', True, 400, '2026-10-04T09:00:00Z')
    retiring = doc('111/1', True, 10, '2026-10-04T09:00:00Z', retiring=True)

    # Promotion: нет флага / retiring — сразу; живому — нет; протухшему —
    # только со второго подряд чтения
    check('duty: нет флага — promote', run_once.standby_should_promote(None, 0, now) is True)
    check('duty: retiring — promote', run_once.standby_should_promote(retiring, 0, now) is True)
    check('duty: живой — ждём', run_once.standby_should_promote(live, 9, now) is False)
    check('duty: 1-е протухшее чтение — ждём',
          run_once.standby_should_promote(stale, 1, now) is False)
    check('duty: 2-е протухшее чтение — promote',
          run_once.standby_should_promote(stale, 2, now) is True)

    # Due-backoff: <3 провалов — работаем; 3 свежих — пропуск 1ч; старые (>1ч) — снова работаем
    fresh_ts = (now - timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%SZ')
    hour2_ts = (now - timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
    old_ts = (now - timedelta(hours=4)).strftime('%Y-%m-%dT%H:%M:%SZ')
    check('duty: 2 провала — работаем',
          run_once.due_skip_info({'k': {'n': 2, 'first_utc': fresh_ts}}, 'k', now) is False)
    check('duty: 3 свежих провала — пропуск',
          run_once.due_skip_info({'k': {'n': 3, 'first_utc': fresh_ts}}, 'k', now) is True)
    check('duty: 3 провала 2ч назад — снова работаем (скип 1ч, не 3ч)',
          run_once.due_skip_info({'k': {'n': 3, 'first_utc': hour2_ts}}, 'k', now) is False)
    check('duty: 3 старых провала — снова работаем',
          run_once.due_skip_info({'k': {'n': 3, 'first_utc': old_ts}}, 'k', now) is False)
    check('duty: нет записи — работаем',
          run_once.due_skip_info({}, 'k', now) is False)

    # Inbox-попытки: рост до 5, exhausted на пределе; без state — финал сразу
    mem = {'state': {}}
    for i in range(1, 5):
        n, exh = run_once._attempt_bump(mem, 777)
        check(f'duty: попытка {i} — не exhausted', (n, exh) == (i, False), (n, exh))
    check('duty: номер предстоящей — 5', run_once._attempt_number(mem, 777) == 5)
    n, exh = run_once._attempt_bump(mem, 777)
    check('duty: 5-я — exhausted', (n, exh) == (5, True), (n, exh))
    check('duty: без state — финал сразу',
          run_once._attempt_bump({}, 777) == (1, True))
    run_once._attempt_clear(mem, 777)
    check('duty: clear сбрасывает', run_once._attempt_number(mem, 777) == 1)

    # merge_states: union completed + max счётчиков + свежий last_run
    local = {'completed': {'a': '2026-10-04'}, 'inbox_attempts': {'m1': {'n': 2, 'ts': 'x'}},
             'due_fails': {}, 'last_run_utc': '2026-10-04T08:00:00Z'}
    remote = {'completed': {'b': '2026-10-04'}, 'inbox_attempts': {'m1': {'n': 4, 'ts': 'y'}},
              'due_fails': {'k': {'n': 1, 'first_utc': 'z'}}, 'last_run_utc': '2026-10-04T09:00:00Z'}
    m = run_once.merge_states(local, remote)
    check('duty: merge completed union',
          m['completed'] == {'a': '2026-10-04', 'b': '2026-10-04'}, m['completed'])
    check('duty: merge attempts max', m['inbox_attempts']['m1']['n'] == 4, m['inbox_attempts'])
    check('duty: merge due_fails union', 'k' in m['due_fails'], m['due_fails'])
    check('duty: merge last_run свежий',
          m['last_run_utc'] == '2026-10-04T09:00:00Z', m['last_run_utc'])

    # prune счётчиков: старше 3 суток — вылетают, свежие — живут
    st = {'completed': {},
          'inbox_attempts': {'old': {'n': 5, 'ts': '2026-09-01T00:00:00Z'},
                             'new': {'n': 1, 'ts': _now_utc()}},
          'due_fails': {'old': {'n': 3, 'first_utc': '2026-09-01T00:00:00Z'}}}
    run_once.prune_state(st)
    check('duty: prune чистит старые счётчики',
          'old' not in st['inbox_attempts'] and 'new' in st['inbox_attempts']
          and 'old' not in st['due_fails'], st)

    # Курсор ротации при merge: побеждает максимум (без отката)
    check('duty: merge курсор max (7,3)',
          run_once.merge_states({'google_key_cursor': 3},
                                {'google_key_cursor': 7})['google_key_cursor'] == 7)
    check('duty: merge курсор max (9,4)',
          run_once.merge_states({'google_key_cursor': 9},
                                {'google_key_cursor': 4})['google_key_cursor'] == 9)

    # _verify_push: всё на месте — True без лишних PUT; дыра — повторный PUT
    saved_get = run_once.gh_state_file_get
    saved_put = run_once.gh_state_file_put
    puts = []
    remote_doc = {'completed': {'a': '2026-10-04'}}
    try:
        run_once.gh_state_file_get = lambda p: (dict(remote_doc), 'sha1')
        run_once.gh_state_file_put = lambda p, t, sha=None, message=None: (
            puts.append((p, message)) or True)
        check('duty: verify ок — True',
              run_once._verify_push('/tmp/x', {'completed': {'a': '2026-10-04'}}) is True
              and puts == [], puts)
        check('duty: verify дыра — повторный PUT и True',
              run_once._verify_push('/tmp/x', {'completed': {'a': '2026-10-04',
                                                             'b': '2026-10-04'}}) is True
              and len(puts) == 1 and 'verify-retry' in puts[0][1], puts)
        run_once.gh_state_file_get = lambda p: (None, None)
        check('duty: verify без ветки — False',
              run_once._verify_push('/tmp/x', {'completed': {'a': 'x'}}) is False)
    finally:
        run_once.gh_state_file_get = saved_get
        run_once.gh_state_file_put = saved_put


def test_heartbeat_loop():
    """Фоновый heartbeat бьёт по времени (не по итерациям) и останавливается отменой."""
    saved_sec = run_once.LEADER_HEARTBEAT_SEC
    saved_claim = run_once.leader_claim
    saved_push = run_once.push_state_best_effort
    saved_work = run_once.work_claim
    calls = {'claim': 0, 'push': 0, 'work': 0}
    me = {'run_id': 't', 'ready': True, 'heartbeat_utc': 'old',
          'watch_started_utc': '2026-10-04T09:00:00Z'}
    try:
        run_once.LEADER_HEARTBEAT_SEC = 0.05
        run_once.leader_claim = lambda doc: calls.__setitem__('claim', calls['claim'] + 1) or True
        run_once.push_state_best_effort = lambda path: calls.__setitem__('push', calls['push'] + 1) or True
        run_once.work_claim = lambda doc: calls.__setitem__('work', calls['work'] + 1) or True
        loop = asyncio.get_event_loop()

        async def run_briefly():
            task = asyncio.create_task(run_once._heartbeat_loop(me, '/tmp/x.json'))
            await asyncio.sleep(0.22)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        loop.run_until_complete(run_briefly())
        check('heartbeat: claim+push бились несколько раз за 0.22с',
              calls['claim'] >= 2 and calls['push'] >= 2, calls)
        check('heartbeat: маркер прогресса едет тем же ритмом',
              calls['work'] >= 2, calls)
        check('heartbeat: метка обновлена', me['heartbeat_utc'] != 'old', me['heartbeat_utc'])
    finally:
        run_once.LEADER_HEARTBEAT_SEC = saved_sec
        run_once.leader_claim = saved_claim
        run_once.push_state_best_effort = saved_push
        run_once.work_claim = saved_work


def test_due_cap():
    """Кап due: за вызов — не больше max_tasks, остаток — следующими вызовами."""
    from types import SimpleNamespace
    entries = [
        {'chat_id': -101, 'hour': 6, 'minute': 0, 'period': '1d',
         'post_to_source': False, 'post_as_telegram': False},
        {'chat_id': -102, 'hour': 6, 'minute': 0, 'period': '1d',
         'post_to_source': False, 'post_as_telegram': False},
        {'chat_id': -103, 'hour': 6, 'minute': 0, 'period': '1d',
         'post_to_source': False, 'post_as_telegram': False},
    ]
    calls = []

    class FakeMain:
        SCHEDULE_FILE = 'x'

        @staticmethod
        def load_schedule(path):
            return entries

        @staticmethod
        async def scheduled_analysis_job(chat_id, period, post_to_source,
                                         post_as_telegram=False):
            calls.append(chat_id)
            return True

    saved_handoff = run_once.handoff_enabled
    run_once.handoff_enabled = lambda: False  # без сети в юнит-тесте
    path = '/tmp/opencode/test_due_cap.json'
    if os.path.exists(path):
        os.remove(path)
    state = {'completed': {}, 'last_run_utc': None}
    loop = asyncio.get_event_loop()
    try:
        with _quiet():
            r1 = loop.run_until_complete(
                run_once.run_due_once(FakeMain(), SimpleNamespace(), state, path,
                                      max_tasks=1))
            r2 = loop.run_until_complete(
                run_once.run_due_once(FakeMain(), SimpleNamespace(), state, path,
                                      max_tasks=1))
            r3 = loop.run_until_complete(
                run_once.run_due_once(FakeMain(), SimpleNamespace(), state, path,
                                      max_tasks=1))
            r4 = loop.run_until_complete(
                run_once.run_due_once(FakeMain(), SimpleNamespace(), state, path,
                                      max_tasks=1))
        check('due-кап: по одной задаче за вызов', calls == [-101, -102, -103], calls)
        check('due-кап: первые три вызова без неуспехов', (r1, r2, r3) == (0, 0, 0), (r1, r2, r3))
        check('due-кап: четвёртый — нечего делать', r4 == 0 and len(calls) == 3, (r4, calls))
        check('due-кап: все три помечены', len(state['completed']) == 3, state['completed'])
        # Без капа (ручной --due) — всё сразу одним вызовом
        calls.clear()
        state2 = {'completed': {}, 'last_run_utc': None}
        with _quiet():
            r = loop.run_until_complete(
                run_once.run_due_once(FakeMain(), SimpleNamespace(), state2, path))
        check('due-кап: без капа всё сразу', calls == [-101, -102, -103] and r == 0, calls)
    finally:
        run_once.handoff_enabled = saved_handoff


def test_telegraph_toc():
    md_two = (
        '💡 **Вспышка кори в США**\n'
        '*Ключевая идея.*\n'
        '\n'
        '[User](https://t.me/c/123/1): суть\n'
        '\n'
        '---\n'
        '\n'
        '💡 **Токен LayerZero (ZRO) & рынок**\n'
        '*Ключевая идея.*\n'
        '\n'
        '[User](https://t.me/c/123/2): суть\n'
    )
    titles = bot.extract_topic_titles(md_two)
    check('toc: разбор двух заголовков',
          titles == ['Вспышка кори в США', 'Токен LayerZero (ZRO) & рынок'], titles)
    check('toc: пустой ввод — пусто', bot.extract_topic_titles('') == [])
    check('toc: строки без эмодзи — мимо',
          bot.extract_topic_titles('Просто текст\n---\n') == [])
    check('toc: снятие разметки зеркалит ветку h3',
          bot.extract_topic_titles('💡 **A *B* C**') == ['A B C'],
          bot.extract_topic_titles('💡 **A *B* C**'))

    check('toc: слаг пробелы->дефисы',
          bot.telegraph_slug('Вспышка кори в США') == '💡-Вспышка-кори-в-США',
          bot.telegraph_slug('Вспышка кори в США'))
    check('toc: слаг держит «ёлочки» и скобки',
          bot.telegraph_slug('Угроза «жизни» (ZRO)') == '💡-Угроза-«жизни»-(ZRO)',
          bot.telegraph_slug('Угроза «жизни» (ZRO)'))
    check('toc: слаг экранирует &',
          bot.telegraph_slug('Слеш & проверка') == '💡-Слеш-&amp;-проверка',
          bot.telegraph_slug('Слеш & проверка'))

    check('toc: один топик — без содержания', bot.build_toc_html(['Один']) == '')
    check('toc: пусто — без содержания', bot.build_toc_html([]) == '')
    toc = bot.build_toc_html(titles)
    check('toc: два пункта', toc.count('<li>') == 2, toc)
    check('toc: href первого', 'href="#💡-Вспышка-кори-в-США"' in toc, toc)
    check('toc: & в href с двойным экранированием',
          'href="#💡-Токен-LayerZero-(ZRO)-&amp;amp;-рынок"' in toc, toc)
    check('toc: текст пункта экранирован',
          'ZRO) &amp; рынок' in toc, toc)
    check('toc: только разрешённые API теги',
          all(bad not in toc for bad in ('<div', '<span', '<h1', '<h2', 'id=')), toc)

    html_two = bot.convert_markdown_to_html(md_two)
    check('toc: содержание в Telegraph-вёрстке',
          '<h4>Содержание</h4>' in html_two and '<ol>' in html_two, html_two[:200])
    check('toc: href пережил санитарку',
          'href="#💡-Вспышка-кори-в-США"' in html_two, html_two[:400])
    check('toc: содержание до первого топика',
          html_two.index('Содержание') < html_two.index('<h3>'))
    md_one = '💡 **Один топик**\n*Идея.*\n'
    check('toc: один топик — без содержания',
          'Содержание' not in bot.convert_markdown_to_html(md_one))
    tg_html = bot.convert_markdown_to_html(md_two, for_telegram=True)
    check('toc: Telegram-ветка — без содержания',
          'Содержание' not in tg_html and '<ol>' not in tg_html, tg_html[:200])
    dup = bot.build_toc_html(['Повтор', 'Повтор'])
    check('toc: дубли — одинаковый href (прыжок на первое вхождение)',
          dup.count('href="#💡-Повтор"') == 2, dup)


def test_dedupe_topics():
    def blk(name, *ids):
        cites = '\n'.join(f'[{name}](https://t.me/c/1/{i}): суть' for i in ids)
        return f'💡 **{name}**\n*Идея.*\n\n{cites}'

    d = bot.dedupe_topics_across_chunks

    one = [(1, 10, blk('A', 1) + '\n---\n' + blk('B', 2), False)]
    check('dedupe: один чанк — без изменений', d(one) == one, d(one))

    c1 = (1, 10, blk('A', 1) + '\n---\n' + blk('B', 2), False)
    c2 = (8, 20, blk('A2', 1) + '\n---\n' + blk('C', 3), False)
    out = d([c1, c2])
    check('dedupe: дубль с подмножеством id удалён',
          'A2' not in out[1][2] and 'C' in out[1][2], out[1][2])
    check('dedupe: первая (ранняя) тема сохранена',
          'B' in out[0][2] and 'A' in out[0][2], out[0][2])

    # надмножество — не дубль, сохраняем
    c3 = (8, 20, blk('A2', 1, 9) + '\n---\n' + blk('C', 3), False)
    out3 = d([c1, c3])
    check('dedupe: надмножество id сохранено', 'A2' in out3[1][2], out3[1][2])

    # не более одного удаления на пару чанков
    c4 = (8, 20,
          blk('D1', 1) + '\n---\n' + blk('D2', 2) + '\n---\n' + blk('E', 3), False)
    out4 = d([c1, c4])
    check('dedupe: не более одного удаления на пару чанков',
          'D1' not in out4[1][2] and 'D2' in out4[1][2] and 'E' in out4[1][2],
          out4[1][2])

    # три чанка: по одному контролю на каждую пару (2 удаления на 3 чанка)
    t1 = (1, 10, blk('A', 1), False)
    t2 = (8, 20, blk('dup1', 1) + '\n---\n' + blk('B', 2), False)
    t3 = (18, 30, blk('dup2', 2) + '\n---\n' + blk('C', 3), False)
    out5 = d([t1, t2, t3])
    check('dedupe: 3 чанка — по одному контролю на пару',
          'dup1' not in out5[1][2] and 'dup2' not in out5[2][2]
          and 'B' in out5[1][2] and 'C' in out5[2][2], out5)

    # тема без ссылок сохраняется (дедуп невозможен)
    c5 = (8, 20, '💡 **Без ссылок**\n*Идея.*', False)
    out6 = d([c1, c5])
    check('dedupe: блок без ссылок сохранён', 'Без ссылок' in out6[1][2], out6[1][2])

    # ошибочная часть не трогается
    err = (8, 20, '⚠️ Чанк пропущен', True)
    out7 = d([c1, err])
    check('dedupe: ошибочный чанк не тронут', out7[1] == err, out7[1])


def test_chunk_rebalance():
    def m(mid, text_len):
        return {'message_id': mid, 'text': 'x' * text_len}

    check('overlap: абсолютный дефолт (60k -> 3000)',
          bot.chunk_overlap_for(60000) == 3000, bot.chunk_overlap_for(60000))
    check('overlap: не растёт с размером чанка',
          bot.chunk_overlap_for(100000) == 3000, bot.chunk_overlap_for(100000))
    check('overlap: потолок 1/4 на малых лимитах',
          bot.chunk_overlap_for(8000) == 2000, bot.chunk_overlap_for(8000))
    check('overlap: CHUNK_OVERLAP_CHARS абсолютный',
          bot.CHUNK_OVERLAP_CHARS == 3000, bot.CHUNK_OVERLAP_CHARS)

    # 9 сообщений по 100 символов + одно на 151 => жадная нарезка даёт
    # чанки 900 и 251 символа; последний меньше половины лимита.
    msgs = [m(i, 68) for i in range(1, 10)] + [m(10, 113)]
    chunks = bot.split_messages_by_chars(msgs, max_chars=1000, overlap_chars=100)
    check('rebalance: число чанков сохранено', len(chunks) == 2, len(chunks))
    last_size = bot.estimate_messages_size(chunks[1][0])
    first_size = bot.estimate_messages_size(chunks[0][0])
    check('rebalance: последний чанк больше не огрызок', last_size >= 500, last_size)
    check('rebalance: два чанка примерно равны', abs(first_size - last_size) <= 60,
          (first_size, last_size))
    check('rebalance: границы пересчитаны',
          (chunks[0][1], chunks[0][2], chunks[1][1], chunks[1][2]) == (1, 6, 6, 10),
          (chunks[0][1:], chunks[1][1:]))
    check('rebalance: перехлёст между чанками сохранён',
          chunks[1][0][0]['message_id'] == msgs[5]['message_id'],
          chunks[1][0][0].get('message_id'))

    # достаточно полный последний чанк не трогаем
    ch_full = [
        ([m(1, 68), m(2, 68)], 1, 3),
        ([m(5, 68), m(6, 68), m(7, 68), m(8, 68), m(9, 68), m(10, 113)], 3, 9),
    ]
    check('rebalance: полный последний чанк не тронут',
          bot.rebalance_last_two_chunks(ch_full, msgs, 1000, 100) is ch_full)
    check('rebalance: один чанк не трогается',
          bot.rebalance_last_two_chunks([(msgs, 1, 10)], msgs, 1000, 100)[0][0] is msgs)


def _now_utc():
    from datetime import timezone
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _quiet():
    """Контекстный менеджер: подавить stdout для тихих прогонов."""
    import contextlib
    import io
    return contextlib.redirect_stdout(io.StringIO())


if __name__ == '__main__':
    main()