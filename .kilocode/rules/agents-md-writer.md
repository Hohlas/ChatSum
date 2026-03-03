---
name: agents-md-writer
description: Use when creating or refactoring AGENTS.md files - enforces best practices for size, structure, and content organization
---

# AGENTS.md Writer

Creates and refactors AGENTS.md files following official Anthropic best practices (2025).

## Golden Rules

| Rule | Why |
|------|-----|
| **AGENTS.md < 200 lines** | Loads on EVERY request, costs tokens |
| **Rules files < 500 lines each** | Official recommendation per file |
| **Critical rules FIRST** | Top = highest priority |
| **Modular rules → `.kilocode/rules/`** | Conditional loading, organized |
| **Use `paths:` frontmatter** | Load rules only for matching files |
| **No linting rules** | Use ESLint/Prettier/Biome instead |
| **Pointers over copies** | Files change, references stay valid |

## Memory Hierarchy

AI agents load memory in this order (higher = higher priority):

| Priority | Type | Location |
|----------|------|----------|
| Highest | Project | `./AGENTS.md` |
| ↓ | Rules | `./.kilocode/rules/*.md` (conditional) |
| ↓ | User | `~/.kilocode/*.md` |
| Lowest | Local | `./AGENTS.local.md` (gitignored) |

Use `/memory` command to see currently loaded files.

## 3-Tier Documentation System

Official recommendation for large projects:

| Tier | Location | Loads | Target |
|------|----------|-------|--------|
| **1. Foundation** | `AGENTS.md` | Always | < 200 lines |
| **2. Component** | `.kilocode/rules/{component}/` | When working in component | < 500 lines |
| **3. Feature** | Co-located with code | When working on feature | As needed |

Example structure:
```
./AGENTS.md                   # Tier 1: always loaded
.kilocode/
└── rules/
    ├── database.md           # Tier 2: SQL, migrations
    ├── api.md                # Tier 2: API patterns
    └── ml/                   # Tier 2: subdirectory
        ├── models.md         # paths: ML/models/**/*.py
        ├── training.md       # paths: ML/train.py
        └── data_loader.md    # paths: ML/data_loader.py
```

## Structure Template

```markdown
# Project Name

One-line description.

## Commands

- `python processing/label_main.py --input Nero.csv` - Preprocessing
- `python ML/train.py --config config.yaml` - Train model
- `jupyter notebook` - Run notebooks

## Architecture

| Path | Purpose |
|------|---------|
| `processing/` | Data preprocessing |
| `ML/` | Machine learning |
| `MT/MQL4/` | MetaTrader 4 code |

## Key Patterns

**Pattern Name**: One-line explanation.

## Modular Docs

See `docs/` for:
- `docs/ml/` - ML documentation (neural_networks.md, baseline_experiments.py.md)
- `docs/mql4/` - MQL4 documentation (lib_PIC.mqh.md)
- `docs/data_preprocessing/` - Data pipeline docs (label_main.py.md, normalize.py.md)
- `docs/data_analysis/` - Statistics docs (statistics.py.md, EDA.ipynb.md)

See `.kilocode/rules/` for:
- `000-documentation.md` - Documentation standards
- `004-mql4-specifics.md` - MQL4 encoding rules
- `007-no-csv-context.md` - CSV handling rules

## Tech Stack

Python 3.11+, PyTorch, Pandas, MQL4
```

## Conditional Rules (Path-Specific)

Use YAML frontmatter for file-type-specific rules:

```markdown
---
paths: "ML/**/*.py"
---

# ML Rules

- All models must have docstrings
- Use standard metrics from utils.py
```

### Glob Patterns

| Pattern | Matches |
|---------|---------|
| `**/*.py` | All .py files anywhere |
| `ML/**/*.py` | Files in ML/ directory |
| `MT/MQL4/**/*.{mq4,mqh}` | MQL4 files |
| `processing/*.py` | Scripts in processing/ |

### Combining Patterns

```yaml
# Multiple extensions
paths: "ML/**/*.{py,ipynb}"

# Multiple directories
paths: "{processing,statistics}/**/*.py, ML/**/*.py"
```

**Note:** Wrap patterns in quotes for YAML safety.

Rules with `paths:` only load when working with matching files → saves tokens.

## Workflow: New Project

1. Create base AGENTS.md
2. Review and trim generated content
3. Identify critical rules — what breaks if ignored?
4. Create `.kilocode/rules/` for domain-specific docs
5. Keep main file < 100 lines

## Workflow: Refactor Existing

1. **Count lines** — if > 300, must split
2. **Find task-specific content** — SQL, debugging, deploy → extract
3. **Create `.kilocode/rules/`**:
   - `ml.md` - ML patterns, models (paths: "ML/**/*.py")
   - `mql4.md` - MQL4 coding rules (paths: "MT/MQL4/**/*.{mq4,mqh}")
   - `data_processing.md` - pipeline rules (paths: "processing/**/*.py")
4. **Use `@file` references** — don't duplicate
5. **Keep in AGENTS.md** — only what applies to EVERY task

## What Goes Where

| Content | Location |
|---------|----------|
| Project description | AGENTS.md |
| Critical constraints | AGENTS.md (top!) |
| Quick start (3 commands) | AGENTS.md |
| Architecture overview | AGENTS.md |
| Key patterns (1-liners) | AGENTS.md |
| ML documentation | `docs/ml/` |
| MQL4 documentation | `docs/mql4/` |
| Data preprocessing docs | `docs/data_preprocessing/` |
| Data analysis docs | `docs/data_analysis/` |
| ML coding rules | `.kilocode/rules/ml.md` (if created) |
| MQL4 coding rules | `.kilocode/rules/004-mql4-specifics.md` |
| Data pipeline rules | `.kilocode/rules/data_processing.md` (if created) |
| API documentation | `docs/ml/neural_networks.md` |
| Personal preferences | `AGENTS.local.md` (gitignored) |
| Code style rules | `.eslintrc` / `biome.json` (NOT docs) |

## Import Syntax

Reference files instead of duplicating:

```markdown
@README.md
@docs/DATA_FLOW.md
@~/.kilocode/snippets/common.md
```

- Relative: `@docs/file.md`
- Absolute: `@~/path/file.md`
- Max depth: 5 hops

## AGENTS.local.md

Personal project settings (auto-gitignored):

```markdown
# My Local Settings

- Prefer verbose output
- Run tests after every change
- My worktree location: .trees/
```

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| 500+ lines | Split into `.kilocode/rules/` |
| SQL examples inline | → `rules/database.md` |
| "Run prettier" rules | Use tool config files |
| Full API docs | → `rules/api.md` |
| Deployment instructions | → `rules/deploy.md` |
| Code in AGENTS.md | Use `@file:line` references |
| Negative rules only | Add alternatives: "Don't X; use Y instead" |

## Quality Checklist

Before finishing:

- [ ] AGENTS.md < 200 lines?
- [ ] Each rules file < 500 lines?
- [ ] Critical rules at top?
- [ ] No task-specific content in main file?
- [ ] No code style rules (use ESLint/Prettier)?
- [ ] `.kilocode/rules/` for domain-specific docs?
- [ ] Subdirectories for components (ml/, mql4/)?
- [ ] `paths:` frontmatter for conditional loading?
- [ ] `@` references instead of duplication?
- [ ] AGENTS.local.md for personal prefs?

## Useful Commands

| Command | Purpose |
|---------|---------|
| `wc -l AGENTS.md` | Check file size |
| `wc -l .kilocode/rules/*.md` | Check rules sizes |
| `/memory` | View loaded memory files |

## Sources

Official:
- code.claude.com/docs/en/memory (Memory management, paths, globs)
- anthropic.com/engineering/claude-code-best-practices
- claudefa.st/blog/guide/mechanics/rules-directory
- humanlayer.dev/blog/writing-a-good-claude-md

Updated: Mar 2026
