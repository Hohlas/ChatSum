"""Пробник Google API ключей: по одному минимальному запросу на ключ.

Проверяет работоспособность каждого ключа отдельно: жив, квота, доступ.
Порядок ключей — ровно как в проде (load_google_api_keys), индексы (N/9)
совпадают с логами. Полные ключи НИКОГДА не печатаются, только маска.

Запуск из корня репо:
  ./venv/bin/python tools/probe_keys.py

Расход: 1 крошечный запрос на ключ (свой проект, своя квота).
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    asyncio.get_running_loop()
except RuntimeError:
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

from dotenv import load_dotenv

load_dotenv('private.txt', override=False)

import main as bot  # noqa: E402
from openai import AsyncOpenAI, APIStatusError, AuthenticationError  # noqa: E402


def quota_details(exc):
    """Вытащить quotaId/limit/retry из тела 429. Best-effort, мусор → {}."""
    out = {}
    try:
        body = exc.response.json()
    except Exception:
        return out
    try:
        err = (body.get('error') or {}) if isinstance(body, dict) else {}
        for d in err.get('details') or []:
            if not isinstance(d, dict):
                continue
            for v in d.get('violations') or []:
                if not isinstance(v, dict):
                    continue
                if v.get('quotaId'):
                    out['quotaId'] = v['quotaId']
                if v.get('quotaMetric'):
                    out['quotaMetric'] = v['quotaMetric']
            if d.get('retryDelay') and 'retry' not in out:
                out['retry'] = d['retryDelay']
    except Exception:
        pass
    return out


async def probe_one(client, model, idx, total):
    t0 = time.monotonic()
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{'role': 'user', 'content': 'Reply with exactly: ok'}],
        )
        dt = int((time.monotonic() - t0) * 1000)
        text = ''
        try:
            text = (resp.choices[0].message.content or '').strip()[:20]
        except Exception:
            pass
        return f"OK ({dt} мс, ответ: {text!r})"
    except AuthenticationError as e:
        return f"DEAD — ключ недействителен/нет доступа ({type(e).__name__})"
    except APIStatusError as e:
        code = getattr(e, 'status_code', '?')
        if code == 429:
            q = quota_details(e)
            qid = q.get('quotaId', '?')
            retry = q.get('retry', '?')
            return f"QUOTA 429 — {qid}, retry {retry}"
        if code in (401, 403):
            return f"DEAD — доступ запрещён (HTTP {code})"
        return f"HTTP {code} — {type(e).__name__}"
    except Exception as e:
        name = type(e).__name__
        if 'timeout' in name.lower():
            return "TIMEOUT — ответа нет 60с"
        return f"{name}: {str(e)[:100]}"


async def amain():
    keys = bot.load_google_api_keys()
    model = (bot.GEMINI_DEFAULT_MODEL or '').strip()
    if not keys:
        print("Нет ключей (GOOGLE_API_KEY* / GOOGLE_API_KEYS).")
        return 2
    if not model:
        print("Не задана GEMINI_MODEL.")
        return 2
    print(f"Ключей: {len(keys)}, модель: {model}, по 1 запросу на ключ.\n")
    results = []
    for i, key in enumerate(keys, 1):
        client = AsyncOpenAI(
            api_key=key,
            base_url='https://generativelanguage.googleapis.com/v1beta/openai/',
            max_retries=0,
            timeout=60.0,
        )
        try:
            verdict = await probe_one(client, model, i, len(keys))
        finally:
            try:
                await client.close()
            except Exception:
                pass
        line = f"[{i}/{len(keys)}] {bot.mask_api_key(key)} → {verdict}"
        print(line, flush=True)
        results.append(verdict)
    ok = sum(1 for r in results if r.startswith('OK'))
    print(f"\nИтог: OK {ok}/{len(results)}")
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(amain()))
