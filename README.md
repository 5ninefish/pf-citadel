# PF Citadel

An AI-governed operating surface for a founder-led company. Citadel is a local web interface that routes messages to a council of AI models — Claude, Gemini, Grok, DeepSeek, and local Gemma — with shared context, persistent memory, and a daily token budget.

Built by [PeakForge](https://github.com/5ninefish) in Kapolei, Hawaii.

---

## What it is

Citadel is the CEO's primary daily operating surface. It replaces the "chat with one AI" pattern with a structured council:

- **Multi-model routing** — each committee (council, health, tech, trading, secops, etc.) routes to the right model mix
- **Shared context** — all models receive the same base context: founding brief, architecture spec, session date, and L2 memory hits
- **Prompt caching** — static context is cached at the provider level (T3); dynamic/query-dependent content is billed fresh
- **Token budget gate** — a daily USD cap prevents runaway spend from automated runners
- **Slash commands** — `/task`, `/propose`, `/decide`, `/wiki`, `/file`, `/fix`, and more
- **Autonomous fix loop** — `#fix <description>` routes to a Claude + Aider loop that drafts and applies code changes

## Architecture

```
chat_server.py     FastAPI server — receives messages, dispatches slash commands
router.py          Multi-model routing, slash command handlers, autonomous fix loop
context.py         Context builder — static (cached) + dynamic split for T3 caching
db.py              SQLite message history per committee
token_budget.py    Daily spend tracker and budget gate
config.py          All path and environment configuration — start here
ui/                Citadel frontend (HTML/CSS/JS)
```

## Setup

**1. Clone and install**
```bash
git clone https://github.com/5ninefish/pf-citadel.git
cd pf-citadel
pip install -r requirements.txt
```

**2. Configure**
```bash
cp .env.example .env
# Edit .env — set PF_ECOSPHERE_ROOT and at least one AI provider key
```

**3. Run**
```bash
uvicorn chat_server:app --host 127.0.0.1 --port 8520 --reload
```

Open `ui/index.html` in a browser, or serve it via the FastAPI static mount.

## Context architecture

`context.py` implements a static/dynamic split for prompt caching:

- **Static context** (file-based, cached): founding brief, canonical spec, env inventory, latest EOD, drift register, scavenge index
- **Dynamic context** (query-dependent, billed fresh): gbrain L2 search hits, TF-IDF semantic retrieval, recent committee messages, session date

The split means ~38K tokens of base context are cached across calls; only the ~2K dynamic layer is billed fresh per message.

## L2 memory

Citadel optionally integrates with [gbrain](https://github.com/PeakForge/gbrain) as a semantic memory substrate. Set `GBRAIN_BIN` in `.env`. Without it, Citadel falls back to TF-IDF retrieval over local message history.

## Token budget

All automated runners share a daily USD cap via `token_budget.py`. Set `DAILY_TOKEN_BUDGET` in `.env` (default: $0.50). The budget resets at midnight HST.

## Committees

Each committee gets its own message history and model routing:

| Committee | Purpose |
|-----------|---------|
| council | General operating surface — routes to Claude by default |
| health | Health and wellness council |
| tech | Technical/engineering discussions |
| trading | Markets and portfolio (routes to Grok) |
| bizdev | Business development |
| secops | Security operations |
| chronos | Scheduling and time-aware decisions |
| marketing | Brand and content |
| tax | Tax and compliance |

## License

MIT
