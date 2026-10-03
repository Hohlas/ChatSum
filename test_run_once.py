"""Модульные тесты чистой логики run_once.py (без сети/Telegram).

Запуск:
  PYTHONPATH=. TELEGRAM_API_ID=12345 TELEGRAM_API_HASH=x TELEGRAM_PHONE=+1 \
  TELEGRAM_SESSION=<StringSession> GOOGLE_API_KEY=x python3 test_run_once.py
"""

import asyncio
import os
import re
import sys
from datetime import datetime, timedelta

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

    # Тест окна: слот now-3min попадает в [now-15, now]
    due = run_once.compute_due(make_entries(now, 3), now, 15)
    check('slot now-3min c lag=15 выполняется', len(due) == 1, due)

    # Слот now-20min НЕ попадает в [now-15, now]
    due = run_once.compute_due(make_entries(now, 20), now, 15)
    check('slot now-20min c lag=15 НЕ выполняется', len(due) == 0, due)

    # Тот же слот now-20min попадает при lag=30
    due = run_once.compute_due(make_entries(now, 20), now, 30)
    check('slot now-20min c lag=30 выполняется', len(due) == 1, due)

    # Полночь МСК: слот 23:58 пред. суток, now=00:03 — окно пересекает полночь
    entries = [{
        'chat_id': -100, 'hour': 23, 'minute': 58,
        'period': '1d', 'post_to_source': False, 'post_as_telegram': False,
    }]
    now_mid = datetime(2026, 10, 2, 0, 3)
    due = run_once.compute_due(entries, now_mid, 15)
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
    test_inbox_v2_flows()
    test_topic_scan()
    test_topic_scan_fail()
    test_key_cursor()
    test_503_rotates_key()

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
    check('watch defaults', a.watch and a.watch_seconds == 480 and a.poll_interval == 60, a)
    a = run_once.parse_args(['--watch', '--watch-seconds', '120', '--poll-interval', '10'])
    check('watch custom', a.watch_seconds == 120 and a.poll_interval == 10, a)


def test_inbox_no_poison():
    """Битое 'sum abh' и слово 'summer' не отравляют опрос: валидная
    команда со ссылкой в том же батче выполняется и удаляется."""
    class FakeMsg:
        def __init__(self, id, text, reply_to_top_id=None):
            self.id = id
            self.text = text
            self.reply_to_top_id = reply_to_top_id

    class FakeEntity:
        title = 'SrcChat'

    class FakeClient:
        def __init__(self, msgs):
            self.msgs = msgs
            self.deleted = []

        async def iter_messages(self, dest, limit=None, min_id=None):
            for m in self.msgs:
                if min_id is not None and m.id <= min_id:
                    continue
                yield m

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
    """Честные фейки inbox (видимость как в проде):

    - iter_messages отдаёт ТОЛЬКО General (прод: GetHistory без топика);
    - топики — через get_messages(ids=) и GetForumTopics(top_message);
    - send_message пишет в client.sent для проверки диагностики.
    """
    from types import SimpleNamespace

    class FakeMsg:
        def __init__(self, id, text, reply_to_top_id=None):
            self.id = id
            self.text = text
            self.reply_to_top_id = reply_to_top_id

    class FakeEntity:
        def __init__(self, title):
            self.title = title

    class FakeClient:
        def __init__(self):
            self.general = []   # [(id, text, top)] — только General
            self.pool = {}      # id -> FakeMsg (для get_messages по топикам)
            self.topics = []    # (topic_id, title, top_message)
            self.deleted = []
            self.sent = []      # (dest, text, reply_to)

        async def __call__(self, request):
            # GetForumTopicsRequest → объект с .topics (id/title/top_message)
            return SimpleNamespace(topics=[
                SimpleNamespace(id=tid, title=title, top_message=top)
                for tid, title, top in self.topics])

        async def iter_messages(self, dest, limit=None, min_id=None):
            msgs = sorted(self.general, key=lambda m: m.id)
            if min_id is not None:
                msgs = [m for m in msgs if m.id > min_id]
            if limit is not None:
                msgs = msgs[-int(limit):]
            for m in msgs:
                yield m

        async def get_messages(self, dest, ids=None):
            # Только id из запроса: топик-скан не подглядывает чужие сообщения
            return [self.pool[i] for i in (ids or []) if i in self.pool]

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

        def __init__(self, client, run_ok=True):
            self.telegram_client = client
            self.ran = []
            self.run_ok = run_ok

        async def get_or_create_topic(self, name):
            return 1

        async def run_analysis(self, **kw):
            self.ran.append(kw)
            return self.run_ok

    client = FakeClient()
    return FakeMsg, client, FakeMain(client, run_ok=run_ok)


def test_inbox_v2_flows():
    """v2: ссылка в General и команда в топике выполняются и удаляются;
    неизвестный топик и команда без ссылки — провал: удаление + диагностика."""
    import io
    import contextlib
    FakeMsg, client, fake = _make_inbox_fakes()
    client.general = [
        FakeMsg(10, 'sum20 https://t.me/c/1892263845/50'),
        FakeMsg(11, 'sum10', reply_to_top_id=7),
        FakeMsg(12, 'sum5', reply_to_top_id=9),
        FakeMsg(13, 'sum5'),
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
    check('v2: batch consumed', ls == 13 and failed == 2, (ls, failed))
    check('v2: ссылка+топик выполнены', len(fake.ran) == 2, fake.ran)
    by_chat = {kw['chat_id']: kw for kw in fake.ran}
    check('v2: ссылка даёт лимит+чат',
          by_chat.get(-1001892263845, {}).get('limit') == 20, by_chat)
    check('v2: топик даёт чат из диалогов',
          by_chat.get(-100111, {}).get('limit') == 10, by_chat)
    check('v2: удалены все командные (успех и провал)',
          client.deleted == [10, 11, 12, 13], client.deleted)
    check('v2: неизвестный топик залогирован',
          out.count('топик 9 не сопоставлен') == 1, out)
    check('v2: команда без ссылки залогирована',
          out.count('без ссылки') == 1, out)
    # Диагностика провалов: msg 12 → в топик 9 (reply_to=9), msg 13 → General
    diag12 = [t for (_d, t, r) in client.sent if r == 9 and 'не сопоставлен' in t]
    diag13 = [t for (_d, t, r) in client.sent if r is None and 'без ссылки' in t]
    check('v2: диагностика нерезолвленного топика', len(diag12) == 1, client.sent)
    check('v2: диагностика General без ссылки', len(diag13) == 1, client.sent)
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


def test_topic_scan():
    """Пер-топик скан: General топики не видит, скан их ловит;
    первый взгляд = top_message, рост топика = range-добор."""
    import io
    import contextlib
    FakeMsg, client, fake = _make_inbox_fakes()
    client.general = []  # General пуст — топик-сообщения в него не попадают
    client.topics = [(7, 'PairChat', 100)]
    client.pool = {
        100: FakeMsg(100, 'sum10', reply_to_top_id=7),
        101: FakeMsg(101, 'sum5', reply_to_top_id=7),
        102: FakeMsg(102, 'hello', reply_to_top_id=7),
        103: FakeMsg(103, 'sum99'),  # General-сообщение — топик-скан его не берёт
    }
    mem = run_once.new_inbox_mem()
    mem['dialogs'] = {'PairChat': -100111}
    loop = asyncio.get_event_loop()
    with contextlib.redirect_stdout(io.StringIO()):
        ls, gfailed = loop.run_until_complete(
            run_once.poll_inbox_once(fake, None, mem))
        f1 = loop.run_until_complete(
            run_once.poll_topics_once(fake, mem, 'test-inbox'))
    check('scan: опрос General топики не видит', ls is None and gfailed == 0,
          (ls, gfailed))
    check('scan: первый взгляд — только top_message',
          [kw['limit'] for kw in fake.ran] == [10], fake.ran)
    check('scan: топик → чат PairChat',
          fake.ran and fake.ran[0]['chat_id'] == -100111, fake.ran)
    check('scan: команда удалена', client.deleted == [100], client.deleted)
    check('scan: seen закреплён', mem['topics_seen'].get(7) == 100,
          mem['topics_seen'])

    # Топик вырос: 101 (команда) и 102 (не команда) — range-добор от seen+1
    client.topics = [(7, 'PairChat', 102)]
    with contextlib.redirect_stdout(io.StringIO()):
        loop.run_until_complete(run_once.poll_topics_once(fake, mem, 'test-inbox'))
    limits = [kw['limit'] for kw in fake.ran]
    check('scan: рост топика → range-добор', limits == [10, 5], limits)
    check('scan: удалены обе команды', client.deleted == [100, 101],
          client.deleted)
    check('scan: seen обновлён', mem['topics_seen'][7] == 102,
          mem['topics_seen'])

    # Без изменений — пусто (никаких повторных выполнений)
    with contextlib.redirect_stdout(io.StringIO()):
        loop.run_until_complete(run_once.poll_topics_once(fake, mem, 'test-inbox'))
    check('scan: без изменений — ничего', len(fake.ran) == 2, fake.ran)


def test_topic_scan_fail():
    """Провал в топике: команда удаляется, диагностика идёт в топик-источник."""
    import io
    import contextlib
    FakeMsg, client, fake = _make_inbox_fakes(run_ok=False)
    client.topics = [(7, 'PairChat', 200)]
    client.pool = {200: FakeMsg(200, 'sum10', reply_to_top_id=7)}
    mem = run_once.new_inbox_mem()
    mem['dialogs'] = {'PairChat': -100111}
    loop = asyncio.get_event_loop()
    with contextlib.redirect_stdout(io.StringIO()):
        failed = loop.run_until_complete(
            run_once.poll_topics_once(fake, mem, 'test-inbox'))
    check('scan-fail: провал посчитан', failed == 1, failed)
    check('scan-fail: команда удалена', client.deleted == [200], client.deleted)
    # Источник известен → диагностика в топик-результатов источника;
    # фейк get_or_create_topic возвращает 1 = General (reply_to=None).
    diag = [t for (_d, t, r) in client.sent if r is None and '⛔' in t]
    check('scan-fail: диагностика в топик-результатов',
          len(diag) == 1 and 'повтора не будет' in diag[0], client.sent)


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


def test_503_rotates_key():
    """503 после same-key ретраев уводит на следующий ключ; 429 — нет."""
    saved = (bot.GOOGLE_API_KEYS, bot.current_google_key_index,
             bot.google_analysis_counter, bot.google_client, bot.asyncio.sleep)
    saved_set_index = bot.set_google_api_key_index
    sleeps = []
    calls = {'n': 0}
    err503 = {'raise': True}

    async def fake_sleep(sec):
        sleeps.append(sec)

    class FakeCompletions:
        async def create(self, **kw):
            calls['n'] += 1
            if err503['raise'] and calls['n'] <= 3:
                raise Exception(
                    "Error code: 503 - high demand, status UNAVAILABLE")
            if not err503['raise']:
                raise Exception("Error code: 429 - rate limit exceeded")
            return 'OK'

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

        with _quiet():
            result = loop.run_until_complete(bot.execute_gemini_request({}))
        check('503: успех со второго ключа', result == 'OK', result)
        check('503: ротация на ключ 2/3',
              bot.current_google_key_index == 1, bot.current_google_key_index)
        check('503: попыток 4 (3×503 + успех)', calls['n'] == 4, calls)
        check('503: паузы same-key ретраев', sleeps == [10, 20], sleeps)

        # 429: same-key ретраи, ротации НЕТ (все вызовы падают → raise)
        calls['n'] = 0
        sleeps.clear()
        err503['raise'] = False
        bot.current_google_key_index = 0
        try:
            with _quiet():
                loop.run_until_complete(bot.execute_gemini_request({}))
            check('429: должен был raise', False)
        except Exception:
            check('429: без ротации — raise после ретраев',
                  bot.current_google_key_index == 0 and calls['n'] == 3,
                  (bot.current_google_key_index, calls))
    finally:
        (bot.GOOGLE_API_KEYS, bot.current_google_key_index,
         bot.google_analysis_counter, bot.google_client,
         bot.asyncio.sleep) = saved
        bot.set_google_api_key_index = saved_set_index


def _quiet():
    """Контекстный менеджер: подавить stdout для тихих прогонов."""
    import contextlib
    import io
    return contextlib.redirect_stdout(io.StringIO())


if __name__ == '__main__':
    main()