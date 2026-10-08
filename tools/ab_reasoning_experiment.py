"""A/B/C experiment: reasoning_effort = none / low / medium on identical input.

Runs the real summarization pipeline (create_summary) against a saved export
JSON so all three efforts see exactly the same messages and chunking. Prints
the token breakdown (prompt / completion / thinking / total) per effort and
saves the markdown summaries for manual comparison.

Usage (from repo root, venv active, private.txt present):
    ./venv/bin/python tools/ab_reasoning_experiment.py [export.json]

No Telegram calls are made: only the Gemini API is used.
"""

import asyncio
import json
import os
import re
import sys

# main.py builds a TelegramClient at import time (Telethon needs a loop).
try:
    asyncio.get_running_loop()
except RuntimeError:
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as bot  # noqa: E402

DEFAULT_EXPORT = 'tests/export_PoN_Holders_🐋_20261008_071945.json'
EFFORTS = ['none', 'low', 'medium']
MAX_ATTEMPTS = 4          # на один effort при временной перегрузке
RETRY_PAUSE_SEC = 30      # пауза между попытками


def load_raw_messages(path):
    """Export JSON is the optimized structure (id/s/t/r) — rebuild raw dicts."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    raw = []
    for m in data['messages']:
        msg = {
            'message_id': m['id'],
            'sender': m.get('s', ''),
            'text': m.get('t', ''),
        }
        if m.get('r'):
            msg['reply_to'] = m['r']
        raw.append(msg)
    return raw, data.get('metadata', {})


def count_topics(text):
    return len(re.findall(r'(?m)^💡', text))


def run_effort(loop, effort, model, raw, meta, out_dir):
    bot.GEMINI_REASONING_EFFORT = effort
    cfg = bot.get_model_generation_config(model)
    chunks = bot.split_messages_by_chars(
        raw, max_chars=cfg['chunk_max_chars'], overlap_chars=cfg['chunk_overlap_chars']
    )

    summary, usage = '', {}
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n{'=' * 60}\n🧪 effort={effort}: попытка {attempt}/{MAX_ATTEMPTS}, "
              f"чанков={len(chunks)}, max={cfg['chunk_max_chars']}, "
              f"overlap={cfg['chunk_overlap_chars']}\n{'=' * 60}")
        summary, usage = loop.run_until_complete(bot.create_summary(
            chunks,
            meta.get('chat_id', ''),
            model=model,
            use_reasoning=(effort != 'none'),
            period_start_date=meta.get('period_start'),
        ))
        usage = usage or {}
        if not usage.get('errors'):
            break
        if attempt < MAX_ATTEMPTS:
            print(f"   ⚠️  effort={effort}: ошибки API, пауза {RETRY_PAUSE_SEC}с и повтор...")
            import time
            time.sleep(RETRY_PAUSE_SEC)

    topics = count_topics(summary)
    out_path = os.path.join(out_dir, f'ab_{effort}.md')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(summary)
    ok = not usage.get('errors')
    print(f"💾 {out_path} (тем: {topics}, ok: {ok})")

    return {
        'effort': effort,
        'chunks': len(chunks),
        'topics': topics,
        'ok': ok,
        'prompt': usage.get('prompt_tokens', 0),
        'completion': usage.get('completion_tokens', 0),
        'thinking': usage.get('thinking_tokens', 0),
        'total': usage.get('total_tokens', 0),
    }


def main():
    args = sys.argv[1:]
    export = DEFAULT_EXPORT
    model = bot.GEMINI_DEFAULT_MODEL
    for a in args:
        if a.startswith('gemini'):
            model = a
        else:
            export = a
    raw, meta = load_raw_messages(export)
    out_dir = os.path.dirname(export) or '.'
    # Эксперимент не должен ждать продовые 3 мин на каждый 503-чанк.
    bot.SERVER_OVERLOAD_PAUSE_SEC = 15
    print(f"📥 {export}: {len(raw)} сообщений, чат {meta.get('chat_id')}, "
          f"модель {model}")

    # Один event loop на весь прогон: общий httpx-клиент привязывается к нему
    # при первом запросе и падает с "Event loop is closed" на другом.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        results = [run_effort(loop, e, model, raw, meta, out_dir) for e in EFFORTS]
    finally:
        loop.close()

    print(f"\n{'=' * 60}\n📊 ИТОГ\n{'=' * 60}")
    header = (f"{'effort':>8} {'ok':>3} {'чанк':>5} {'тем':>5} "
              f"{'промпт':>9} {'ответ':>8} {'мышл':>7} {'всего':>9}")
    print(header)
    for r in results:
        print(f"{r['effort']:>8} {str(r['ok']):>3} {r['chunks']:>5} {r['topics']:>5} "
              f"{r['prompt']:>9,} {r['completion']:>8,} {r['thinking']:>7,} {r['total']:>9,}")


if __name__ == '__main__':
    main()
