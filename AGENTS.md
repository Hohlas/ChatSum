# Project goal
ChatSum is a Telegram bot (userbot) that produces AI summaries of chats: on owner command and on schedule. One codebase, two runtime modes (exactly one at a time): a long-lived process on VPS (`python3 main.py`) and short-run duty on GitHub Actions (cron `*/11`, leader/standby via the `state` branch).

# Solution quality
- If inputs are missing or contradictory, say so directly.
- Rely on verifiable primary sources — documents holding first-hand information: official docs, standards, API specifications. When using secondary sources, state their type and origin explicitly.
- Choose pragmatic solutions grounded in best practices. Prefer soundness and accuracy over ease of implementation.
- When alternatives exist, list them with reasoning.
- Prefer facts and well-supported conclusions. Mark assumptions, hypotheses, and ideas explicitly. No guesswork or speculation.

# Dialogue rules
- Treat my ideas critically: do not accept them as truth without verification — I may lack expertise.
- Push back with facts, not opinions. You are not obliged to agree — you are obliged to argue your case.
- Ask clarifying questions when a request is ambiguous or lacks context.

# Response rules
- Answer in plain Russian.
- Explain via goal, reason, and consequence.
- Avoid jargon, loanwords, and narrow terms. If a term is necessary, define it on first use.

## Language rules
- Reason step by step in English.
- Keep code, identifiers, comments, specs, plans, and audit notes in English. Do not translate them.
- Write the final answer to the user in Russian only.

# Mandatory rules
- Before starting a session, read `docs/AlgDescription.md` (design and invariants) and `docs/CONTEXT_HANDOFF.md` (live state). Stable knowledge lives only in AlgDescription, current state only in the handoff — do not duplicate between them. Do not update them unless asked.
- Monolith files: `main.py` (~220 KB), `run_once.py` (~90 KB). For files over 60 KB — targeted reads only (Grep, Read with offset/limit), search via rg by keywords.
- Dependencies — `requirements.txt` (telethon, apscheduler, openai, httpx, telegraph, dotenv). Python >= 3.10, env `./venv`; tests: `./venv/bin/python tests/test_run_once.py` (must stay green).
- For bugfixes, do not refactor "along the way".
- Secrets (`private.txt`, `telegram_session.txt`, `*.session` — in `.gitignore`) — never commit, print, or display.
- Prod is live: GitHub Actions cron picks up `main` within minutes. Runtime edits (`main.py`, `run_once.py`, `.github/workflows/`) go straight to live duty — tests must be green after changes.
- `git commit`, `git push` — only on explicit user request.

# Project layout
.
├── main.py                     # Core: Telegram client, analysis, commands, APScheduler (VPS mode)
├── run_once.py                 # Actions duty: leader/standby, inbox, due tasks, state
├── tests/
│   └── test_run_once.py        # Unit tests (156 PASS; run from repo root)
├── tools/                      # One-off helpers: gen_session.py, get_channel_id.py, legacy test_bot.py
├── scripts/                    # sh: setup, start/stop (screen), push_github_secrets, systemd-unit
├── docs/
│   ├── AlgDescription.md       # Agent onboarding: algorithm, gate numbers, incidents (read first)
│   ├── CONTEXT_HANDOFF.md      # Last-session context (live state)
│   ├── CONFIG_MANAGEMENT.md    # Config formats
│   └── audit.md                # Context audit (not a source of truth)
├── .github/workflows/
│   └── summarize.yml           # Cron */11, timeout 330, no concurrency
├── SCHEDULE.txt                # Slots `chat|HH:MM|limit` (MSK)
├── PROMPT.txt                  # Summary prompt
├── EXCLUDED_USERS.txt / PRIORITY_USERS.txt
├── private.txt.example         # Secrets template (private.txt itself — local only)
├── requirements.txt
└── README.md                   # User docs (setup, both modes)
