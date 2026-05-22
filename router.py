# Dark Factory live 2026-04-09
import os
import re
import json
import time
import httpx
import logging
from pathlib import Path
from dotenv import load_dotenv
from context import build_context, build_context_split
from config import (
    LOGS_DIR as _LOGS_DIR_CFG,
    EMPIRE_STATE_PATH,
    EMPIREWIKI_RAW_DIR,
    EMPIRE_RESEARCH_PATH,
    EMPIRE_RESEARCH_PENDING,
    EMPIRE_IDEAS_PENDING,
    EMPIRE_TASKS_PATH,
    PF_SHARED_DIR,
    CONDA_BIN,
    OPENCLAW_SESSIONS,
    FILE_ACCESS_PREFIX,
    GBRAIN_BIN,
)

load_dotenv()

# ─────────────────────────────────────────────
# API USAGE LOGGING (L6 audit sink — agent_actions.jsonl)
# ─────────────────────────────────────────────
_LOGS_DIR = _LOGS_DIR_CFG
_ACTIONS_LOG = _LOGS_DIR / "agent_actions.jsonl"

def _log_usage(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> None:
    """Append one api_usage line to agent_actions.jsonl. Best-effort; never raises."""
    try:
        _LOGS_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "type": "api_usage",
            "provider": provider,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            "cache_read_tokens": cache_read_tokens,
        }
        with _ACTIONS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as _log_exc:
        import sys
        print(f"[_log_usage ERROR] {_log_exc}", file=sys.stderr, flush=True)
        try:
            with (_LOGS_DIR / "log_errors.txt").open("a") as _ef:
                _ef.write(f"[_log_usage ERROR] {__import__('datetime').datetime.utcnow().isoformat()} {_log_exc!r}\n")
        except Exception:
            pass

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
OPENCLAW_BASE_URL = os.getenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18789")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

logger = logging.getLogger(__name__)

VALID_MENTIONS = {"gec", "grc", "gef", "grf", "nexus", "apex", "clc", "deepseek", "gemma", "horizon", "openclaw"}

def parse_mentions(message: str) -> tuple[list[str], str]:
    """
    Parse ALL @mention prefixes from message.
    Supports multiple mentions: "@gep @grp @claude what do you think?"
    
    Returns:
        tuple: (targets: list[str], cleaned_message: str)
        targets is a list of recognized agent handles in order found.
        If no mentions found, targets is empty list.
        cleaned_message has all @mentions stripped from the start.
    """
    if not message or not isinstance(message, str):
        return [], message

    trimmed = message.lstrip()
    targets = []
    pos = 0

    # Parse mentions greedily from the start of the message
    while pos < len(trimmed) and trimmed[pos] == "@":
        end = pos + 1
        while end < len(trimmed) and not trimmed[end].isspace():
            end += 1
        token = trimmed[pos+1:end].lower()
        if token in VALID_MENTIONS:
            targets.append(token)
            pos = end
            while pos < len(trimmed) and trimmed[pos].isspace():
                pos += 1
        else:
            break

    if not targets:
        return [], message

    cleaned = trimmed[pos:].strip()
    return targets, cleaned

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GROK_API_KEY = os.getenv("XAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
OPENCLAW_GATEWAY_TOKEN = os.getenv("OPENCLAW_GATEWAY_TOKEN", "")
GEMINI_MODEL = os.getenv("GE_PRO_MODEL", "gemini-3.1-pro-preview")
GEMINI_FLASH_MODEL = os.getenv("GE_FLASH_MODEL", "gemini-2.0-flash")
# G-T3-A: Gemini CachedContent URI written by scripts/gemini_cache_bedrock.py
GEMINI_CACHE_URI_PATH = _LOGS_DIR_CFG / "gemini_cache.uri"
GROK_FAST_MODEL = os.getenv("GR_FAST_MODEL", "grok-4-1-fast-non-reasoning")

# Flash committees: high-volume, low-stakes — speed/cost beats depth.
# Pro committees: capital risk, legal exposure, or architectural decisions.
FLASH_COMMITTEES = {"health", "chronos", "marketing", "secops"}

# G-L4-D: on-demand file-read tool for Citadel personas.
# Model emits [READ_FILE: /path] → router reads via cl_bridge → re-calls with content.
# Works uniformly across clc/gec/grc without per-backend function-calling APIs.
READ_FILE_RE = re.compile(r'\[READ_FILE:\s*([^\]\n]+)\]')
READ_FILE_DIRECTIVE = (
    "\n\nFILE READ TOOL: If you need to inspect a file on the PeakForge filesystem "
    "to answer accurately, output exactly `[READ_FILE: /absolute/path/to/file]` "
    "on its own line in your response. The file content will be injected and you "
    "will be re-called with it. Use this instead of asking Dalen to provide files "
    "via @openclaw. Absolute paths only. One file per response."
)

# EMPIRE_STATE_PATH imported from config
LEDGER_PATH = Path(__file__).parent / "COUNCIL_LEDGER.md"

# Primer retired per canonical §3.5 v2.0 (2026-04-22). Context is now composed
# in context.py from founding brief + canonical + ENV_INVENTORY + latest EOD.
# Claude callers receive the full base context via `system` from build_context().

# Committee mandates — expansive, not restrictive. Models know where they are
# and what the full scope of that tab's mission is.
COMMITTEE_CONTEXT = {
    "council":   "You are in the General Council tab. This is Dalen's high-level strategic discussion space, equivalent to how he uses gemini.com or grok.com directly — natural conversation about empire strategy, resource allocation, major decisions, and long-term vision. Respond conversationally. Answer the actual question asked. Do not turn conversational asks into execution tasks. Routing to @openclaw / /cascade is reserved for moments when Dalen has clearly asked for work to be done, not for questions about what to do next.",
    "health":    "You are in the Health committee tab. Your mandate covers all aspects of biomarkers, longevity, physical appearance and aesthetics (body composition, skin, hair, posture, visual presentation), athletic and cognitive performance, recovery, sleep, nutrition, and human capital optimization. You may explore any scientific, technological, lifestyle, supplementation, or aesthetic intervention that improves Dalen's healthspan, looks, physical capability, mental sharpness, executive performance, and overall empire output and resilience.",
    "engine1":   "You are in the Engine 1 (White Peak Industrial) tab. Your mandate is everything related to White Peak Logistics and GovCon operations — supply chain, SAM.gov opportunities, regulatory intelligence, fulfillment, margins, and scaling the core industrial revenue engine. You may explore any adjacent industrial or logistics opportunity that strengthens Engine 1.",
    "trading":   "You are in the Trading committee tab. Your mandate is all trading strategies, bots, revenue generation, and market opportunities — equities, options (0DTE), futures, crypto, perpetuals, statistical arbitrage, volatility, and any consistent revenue edge. HARD RULE: Never recommend a bot change or strategy modification without first requesting the actual file via @openclaw if not already in context. Route execution via @openclaw (immediate) or /cascade (async). @claude is available for architecture review via @mention.",
    "tax":       "You are in the Tax committee tab. Your mandate covers all tax strategy, entity structuring, liability minimization, compliance, international structures, and regulatory optimization. You may explore any legal, accounting, or jurisdictional approach that protects or grows empire capital.",
    "chronos":   "You are in the Chronos (scheduling & correspondence) tab. Your mandate is timing, sequencing, automation, scheduling, crons, LaunchAgents, correspondence, and any system that optimizes when and how the empire executes. You may propose any timing-based or workflow improvement that increases efficiency or reduces risk.",
    "bizdev":    "You are in the BizDev committee tab. Your mandate is business development, new revenue streams, partnerships, expansion opportunities, productization, licensing, and any path that grows the empire beyond its current engines. Think aggressively about scalable, hands-off opportunities.",
    "tech":      "You are in the Tech committee tab. Your mandate is technical architecture, infrastructure, code, and automation. HARD RULE: Never reason about or recommend changes to code you have not seen in this conversation. If a file is not already in context, request it first via @openclaw before responding. Once you have the file, propose a precise change. Route all execution via @openclaw (immediate) or /cascade (async). Defer to @claude for single-author technical decisions. For complex strategy or architecture calls, invoke /plan to trigger the full PydanticCouncil autonomous round-trip.",
    "marketing": "You are in the Marketing committee tab. Your mandate is branding, customer acquisition, positioning, Engine 2 (W.Peak Outdoors) activation, content strategy, and any growth lever that brings in revenue or awareness. You may explore any creative or distribution channel that scales the empire.",
    "secops":    "You are in the SecOps committee tab. Your mandate is security, operations, API key management, audits, compliance, and infrastructure protection. HARD RULE: Never recommend a config or code change without first requesting the actual file via @openclaw if not already in context. Route execution via @openclaw (immediate) or /cascade (async). CRITICAL SAFETY RULE: Never request, display, or reference the contents of .env files or any file containing API keys or tokens. Key audits must only verify presence or absence — never expose values. Keys are managed exclusively via secure file operations on PF, never in Brain UI.",
}

# Hardcoded routing — GEP and GRP never auto-trigger each other
COMMITTEE_ROUTING = {
    "council":   ["gec", "grc"],
    "health":    ["nexus", "apex"],
    "engine1":   ["gec", "grc"],
    "trading":   ["grc", "gec"],
    "tax":       ["gec", "grc"],
    "chronos":   ["gec"],
    "bizdev":    ["gec", "grc"],
    "tech":      ["clc"],
    "marketing": ["gec"],
    "secops":    ["clc"],
}

AGENT_PERSONAS = {
    "gec":      "You are GEC, co-equal council member with GRC and CLC, advising the PeakForge Empire CEO (Dalen). Default mode is conversational — this tab is Dalen's equivalent of talking to you in gemini.com. Answer the actual question. Do not fabricate urgency, do not assemble task lists, do not jump to @openclaw unless Dalen has clearly asked for execution. Read the intent: 'give the next primary task' is a question asking you to name one thing — a one-line answer, not a delegation packet. WEB ACCESS: web_search auto-invokes when current data is needed (canonical §14.0 T1, verified 2026-04-24) — do not claim you cannot verify without #web. SOURCE-CITATION: cite factual claims about external reality (events, prices, statistics, dates, public statements, current state) inline as [src: url-or-gbrain-page-or-file]; reasoning, opinion, and synthesis don't need citations. Uncited factual claims are subject to cross_check flag (G-L4-B). EXECUTION ROUTING (only when Dalen explicitly asks for something to be done): immediate → @openclaw; async (>2 min) → /cascade. No [PROPOSE: ...] blocks ever.",
    "grc":      "You are GRC, co-equal council member with GEC and CLC, advising the PeakForge Empire CEO (Dalen). Default mode is conversational — this tab is Dalen's equivalent of talking to you in grok.com. Answer the actual question. Do not fabricate urgency, do not assemble task lists, do not jump to @openclaw unless Dalen has clearly asked for execution. Read the intent: 'give the next primary task' is a question asking you to name one thing — a one-line answer, not a delegation packet. WEB ACCESS: web_search auto-invokes when current data is needed (canonical §14.0 T1, verified 2026-04-24) — do not claim you cannot verify without #web. SOURCE-CITATION: cite factual claims about external reality (events, prices, statistics, dates, public statements, current state) inline as [src: url-or-gbrain-page-or-file]; reasoning, opinion, and synthesis don't need citations. Uncited factual claims are subject to cross_check flag (G-L4-B). EXECUTION ROUTING (only when Dalen explicitly asks for something to be done): immediate → @openclaw; async (>2 min) → /cascade. No [PROPOSE: ...] blocks ever.",
    "nexus":    "You are Nexus, the Health CMO of the PeakForge Empire. Focus on longevity, biomarkers, and health protocols.",
    "apex":     "You are Apex, the Performance Coach of the PeakForge Empire. Focus on training, body composition, and physical performance.",
    "clc":   "You are CLC, CTO and Builder for the PeakForge Empire. You are a strategic and technical advisor. You have been @mentioned directly by the CEO. Respond with precision and brevity. Technical substance is encouraged — write code snippets, SQL, architecture diagrams, and pseudocode freely during debate. NEVER emit [PROPOSE: ...] blocks. Final execution routes through #fix, @openclaw, or /cascade. For complex strategy or architecture asks, recommend /plan to invoke the full PydanticCouncil round-trip (CLP + GEP + GRP synthesis). SOURCE-CITATION: cite factual claims about external reality (events, prices, statistics, dates, public statements, current state) inline as [src: url-or-gbrain-page-or-file]; reasoning, opinion, and synthesis don't need citations. Uncited factual claims are subject to cross_check flag (G-L4-B). CEO OUTPUT FORMAT (ratified 2026-04-28, mandatory in tech committee): every response must close with this block in plain English — 'Shipped this turn:' (one line per item, omit if nothing shipped), 'CEO action required:' (numbered list; if none, write Nothing required), 'Next move:' (one line, who owns it). Code, file paths, terminal commands, and technical analysis stay above this block and are never repeated inside it.",
    "gef":      "You are GEF, Gemini Flash — fast-tier council member for the PeakForge Empire. You are explicitly called when speed matters over depth. Answer directly and concisely. No preamble, no task lists. Conversational replies only unless asked for structured output.",
    "grf":      "You are GRF, Grok Fast — non-reasoning fast-tier council member for the PeakForge Empire. You are explicitly called when speed matters over depth. Answer directly and concisely. No preamble, no task lists. Conversational replies only unless asked for structured output.",
    "deepseek": "You are DeepSeek, a high-volume draft specialist for the PeakForge Empire. You produce clean, structured drafts on demand. You are read-only — you never execute code or take actions. When asked to draft something, produce it completely and precisely. No preamble, no meta-commentary. Just the deliverable.",
    "gemma":    "You are Gemma, a local privacy-first AI assistant for the PeakForge Empire running on-device. You provide fast, private responses for sensitive tasks. You are read-only — you never execute code or take actions. Respond concisely and directly.",
    "horizon":  "You are Horizon, the PeakForge Empire's live web research agent powered by Tavily. You surface current, real-world intelligence — market data, competitor activity, SAM.gov opportunities, shipping rates, regulatory updates, and any information the council needs beyond training data. You are read-only. Return structured, factual results with sources. No hallucination — only report what the search returned.",
    "openclaw": "You are OpenClaw, the PeakForge Empire's local execution arm. You have full filesystem access to the PF Mac Mini — Ecosphere, Trading, and scripts directories. Engine 1 is explicitly off-limits. You read files, run whitelisted commands, and report results precisely. Always back up before editing any file. Return structured, factual results. No hallucination — only report what you found or executed.",
}

# ─────────────────────────────────────────────
# T3 HELPER — dynamic context injection
# ─────────────────────────────────────────────

def _inject_dynamic_to_user(messages: list[dict], dynamic_system: str) -> list[dict]:
    """Prepend dynamic context to the first user message.

    Used for Grok and Gemini callers that cannot send a second cached system block.
    Keeps system_instruction/system message = static bedrock only, so Grok's prefix
    cache and Gemini's future CachedContent pipeline see an identical prefix on every
    call within a session. Dynamic context (date, gbrain hits, recent messages) rides
    in the user turn instead, which is billed fresh anyway.

    Does not modify the messages list in place — returns a new list.
    """
    if not dynamic_system:
        return messages
    result = []
    injected = False
    for msg in messages:
        if msg["role"] == "user" and not injected:
            result.append({**msg, "content": f"[CONTEXT]\n{dynamic_system}\n\n---\n{msg['content']}"})
            injected = True
        else:
            result.append(msg)
    return result if result else messages


# ─────────────────────────────────────────────
# STANDARD CALLERS (no web search)
# ─────────────────────────────────────────────

async def call_gep(system: str, messages: list[dict], dynamic_system: str = "") -> str:
    """Call Gemini Pro with G-T3-A CachedContent support (2026-04-30).

    If logs/gemini_cache.uri exists (written by scripts/gemini_cache_bedrock.py),
    the static bedrock (~40K tokens) is served from Gemini's cache at ~0.1x cost.
    Dynamic context rides in the user turn via _inject_dynamic_to_user().
    On cache miss/expiry, auto-clears URI and retries uncached.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    augmented = _inject_dynamic_to_user(messages, dynamic_system)
    contents = [{"role": "user", "parts": [{"text": m["content"]}]} for m in augmented if m["role"] == "user"]

    # G-T3-A: use cached bedrock if URI on disk
    cache_name = ""
    if GEMINI_CACHE_URI_PATH.exists():
        cache_name = GEMINI_CACHE_URI_PATH.read_text().strip()

    if cache_name:
        # Cached path: system_instruction is baked into cache — omit it here
        payload = {"cachedContent": cache_name, "contents": contents}
    else:
        payload = {"system_instruction": {"parts": [{"text": system}]}, "contents": contents}

    try:
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post(url, json=payload)
            if not r.is_success and cache_name:
                # Cache likely expired — clear URI and retry uncached
                logger.warning(f"[API] call_gep cached call failed ({r.status_code}) — clearing URI, retrying uncached")
                GEMINI_CACHE_URI_PATH.unlink(missing_ok=True)
                payload = {"system_instruction": {"parts": [{"text": system}]}, "contents": contents}
                r = await client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
            meta = data.get("usageMetadata", {})
            _log_usage(
                provider="google",
                model=GEMINI_MODEL,
                input_tokens=meta.get("promptTokenCount", 0),
                output_tokens=meta.get("candidatesTokenCount", 0),
                cache_read_tokens=meta.get("cachedContentTokenCount", 0),
            )
            return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        logger.warning(f"[API] call_gep error: {e}")
        return f"[GEC error: {str(e)}]"

async def call_gec_flash(system: str, messages: list[dict], dynamic_system: str = "") -> str:
    """GEC on Gemini Flash — health/chronos/marketing/secops committees."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_FLASH_MODEL}:generateContent?key={GEMINI_API_KEY}"
    augmented = _inject_dynamic_to_user(messages, dynamic_system)
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": m["content"]}]} for m in augmented if m["role"] == "user"]
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
            meta = data.get("usageMetadata", {})
            _log_usage(
                provider="google",
                model=GEMINI_FLASH_MODEL,
                input_tokens=meta.get("promptTokenCount", 0),
                output_tokens=meta.get("candidatesTokenCount", 0),
            )
            return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        logger.warning(f"[API] call_gec_flash error: {e}")
        return f"[GEC error: {str(e)}]"

async def call_grp(system: str, messages: list[dict], dynamic_system: str = "") -> str:
    # system message = static bedrock only. Dynamic context prepended to user message
    # so Grok's x-grok-conv-id prefix cache sees an identical prefix on every call.
    # Without this, the changing dynamic context was in the system string, busting
    # the prefix hash on every call despite the conv-id header. (T3 fix, 2026-04-30)
    url = "https://api.x.ai/v1/chat/completions"
    grp_model = os.getenv("GR_PRO_MODEL", "grok-4.3")
    augmented = _inject_dynamic_to_user(messages, dynamic_system)
    payload = {
        "model": grp_model,
        "messages": [{"role": "system", "content": system}] + augmented,
        "stream": False
    }
    grp_headers = {"Authorization": f"Bearer {GROK_API_KEY}", "x-grok-conv-id": "peakforge-grp-v1"}
    _asyncio = __import__("asyncio")
    last_err = None
    for attempt in range(1, 5):  # up to 4 attempts
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(url, json=payload, headers=grp_headers)
                if r.status_code == 429:
                    # Read Retry-After before raising — xAI typically sets it.
                    # Default 20s if header absent; cap at 60s so we don't stall too long.
                    retry_after = int(r.headers.get("retry-after", 20))
                    wait = min(retry_after, 60)
                    logger.warning(f"[API] call_grp 429 rate-limited (attempt {attempt}/4) — waiting {wait}s")
                    if attempt < 4:
                        await _asyncio.sleep(wait)
                    last_err = f"429 rate-limited (Retry-After: {retry_after}s)"
                    continue
                if r.status_code == 503:
                    wait = 2 ** attempt  # 2s, 4s, 8s for infra flaps
                    logger.warning(f"[API] call_grp 503 (attempt {attempt}/4) — waiting {wait}s")
                    if attempt < 4:
                        await _asyncio.sleep(wait)
                    last_err = "503 service unavailable"
                    continue
                r.raise_for_status()
                data = r.json()
                usage = data.get("usage", {})
                _log_usage(
                    provider="xai",
                    model=grp_model,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                )
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            last_err = e
            logger.warning(f"[API] call_grp attempt {attempt}/4 failed: {e}")
            if attempt < 4:
                await _asyncio.sleep(2 ** attempt)
    # GRF fallback: 429/503 exhausted — route to fast tier to keep council unblocked.
    logger.warning(f"[API] call_grp falling back to GRF after {4} failed attempts: {last_err}")
    return await call_grf(system, messages, dynamic_system)

async def call_grf(system: str, messages: list[dict], dynamic_system: str = "") -> str:
    """GRF — Grok fast non-reasoning. Static-first payload for prefix cache parity."""
    url = "https://api.x.ai/v1/chat/completions"
    augmented = _inject_dynamic_to_user(messages, dynamic_system)
    payload = {
        "model": GROK_FAST_MODEL,
        "messages": [{"role": "system", "content": system}] + augmented,
        "stream": False
    }
    grf_headers = {"Authorization": f"Bearer {GROK_API_KEY}", "x-grok-conv-id": "peakforge-grf-v1"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, json=payload, headers=grf_headers)
            if not r.is_success:
                body = r.text[:200]
                logger.warning(f"[API] call_grf HTTP {r.status_code}: {body}")
                return f"[GRF error: HTTP {r.status_code} — {body}]"
            data = r.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content")
            if content is None:
                return f"[GRF error: unexpected response shape — {str(data)[:200]}]"
            return content
    except Exception as e:
        detail = repr(e) if not str(e) else str(e)
        logger.warning(f"[API] call_grf error: {detail}")
        return f"[GRF error: {detail}]"

async def call_claude(system: str, messages: list[dict], dynamic_system: str = "") -> str:
    """Call Anthropic Claude with T3 prompt caching (2026-04-30).

    system        — static context block; receives cache_control: ephemeral.
                    Must be identical across calls within a session for cache to hit.
    dynamic_system — query/message-dependent context; no cache_control.
                    Sent as a second system block so it does not contaminate the
                    cached hash. Defaults to "" (omitted) for callers that don't
                    use build_context_split().
    """
    if not ANTHROPIC_API_KEY:
        return "[Claude error: ANTHROPIC_API_KEY not found in environment]"
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "prompt-caching-2024-07-31",
        "content-type": "application/json"
    }
    user_content = ""
    for msg in messages:
        if msg["role"] == "user":
            user_content = msg["content"]
            break
    # T3 two-block system format:
    # Block 1 (static, cached): persona + mandates + file-based context.
    #   cache_control: ephemeral → Anthropic caches tokens up to and including
    #   this block. Cache hits when block 1 text is identical to a prior call.
    # Block 2 (dynamic, uncached): session date, gbrain hits, recent messages.
    #   No cache_control → always billed fresh, never pollutes the cache hash.
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    if dynamic_system:
        system_blocks.append({"type": "text", "text": dynamic_system})
    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 4000,
        "system": system_blocks,
        "messages": [{"role": "user", "content": user_content}]
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            logger.info(f"[API] call_claude → anthropic claude-sonnet-4-6 (max_tokens=4000)")
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            usage = data.get("usage", {})
            _log_usage(
                provider="anthropic",
                model="claude-sonnet-4-6",
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
                cache_read_tokens=usage.get("cache_read_input_tokens", 0),
            )
            return data["content"][0]["text"]
    except Exception as e:
        return f"[Claude error: {str(e)}]"

async def call_deepseek(system: str, messages: list[dict]) -> str:
    if not DEEPSEEK_API_KEY:
        return "[DeepSeek error: DEEPSEEK_API_KEY not found in environment]"
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    user_content = ""
    for msg in messages:
        if msg["role"] == "user":
            user_content = msg["content"]
            break
    payload = {
        "model": "deepseek-chat",
        "max_tokens": 8192,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content}
        ],
        "stream": False
    }
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            logger.info(f"[API] call_deepseek → deepseek-chat (max_tokens=8192)")
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[DeepSeek error: {str(e)}]"

async def call_gemma(system: str, messages: list[dict]) -> str:
    user_content = ""
    for msg in messages:
        if msg["role"] == "user":
            user_content = msg["content"]
            break
    gemma_model = os.getenv("GEMMA_MODEL", "gemma3:4b")
    payload = {
        "model": gemma_model,
        "prompt": f"System: {system}\n\nUser: {user_content}",
        "stream": False
    }
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
            r.raise_for_status()
            return r.json().get("response", "[Gemma: empty response]")
    except httpx.ConnectError:
        return "[Gemma error: Ollama not running on port 11434. Start with: ollama serve]"
    except Exception as e:
        return f"[Gemma error: {str(e)}]"

async def call_openclaw(system: str, messages: list[dict]) -> str:
    """OpenClaw local execution arm — subprocess via CLI (stable versioned interface).

    Uses persistent 'main' session (same as Telegram) for fast response.
    JSON output lands on stderr by design.
    """
    import asyncio as _asyncio
    import json as _json
    import os as _os
    import glob as _glob

    user_content = ""
    for msg in messages:
        if msg["role"] == "user":
            user_content = msg["content"]
            break

    # Prepend system context to message so agent has full framing
    full_message = f"{system}\n\n---\n{user_content}" if system else user_content

    _claw_env = {
        **_os.environ,
        "PATH": f"{CONDA_BIN}:/usr/local/bin:/usr/bin:/bin",
        "OPENCLAW_GATEWAY_TOKEN": OPENCLAW_GATEWAY_TOKEN,
    }

    # Clear any stale session lock before spawning — prevents "Failed to fetch"
    # after claw_bridge or Brain restarts mid-session
    for _lock in _glob.glob(str(OPENCLAW_SESSIONS / "*.lock")):
        try:
            _os.remove(_lock)
            logger.info(f"[call_openclaw] Cleared stale lock: {_lock}")
        except Exception:
            pass

    try:
        logger.info(f"[API] call_openclaw → openclaw agent main (persistent session)")
        proc = await _asyncio.create_subprocess_exec(
            str(Path(CONDA_BIN) / "openclaw"), "agent",
            "--agent", "main",
            "--message", full_message,
            "--json",
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
            env=_claw_env,
        )
        stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=180)

        # JSON lands on stderr — stdout is empty by design
        raw = stderr.decode("utf-8", errors="replace").strip()

        if not raw:
            raw = stdout.decode("utf-8", errors="replace").strip()

        if not raw:
            return "[OpenClaw: empty response]"

        # Parse JSON — shape: {"result": {"payloads": [{"text": "..."}]}} or flat {"payloads": [...]}
        try:
            json_start = raw.find("{")
            json_str = raw[json_start:] if json_start != -1 else raw
            data = _json.loads(json_str)
            payloads = data.get("result", {}).get("payloads") or data.get("payloads", [])
            if payloads and isinstance(payloads, list):
                reply = "\n\n".join(p.get("text", "") for p in payloads if p.get("text"))
                if reply:
                    return str(reply)
            reply = (
                data.get("reply")
                or data.get("output")
                or data.get("message")
                or data.get("text")
                or raw
            )
            return str(reply)
        except _json.JSONDecodeError:
            return raw if raw else "[OpenClaw: empty response]"

    except _asyncio.TimeoutError:
        return "[OpenClaw error: timeout after 60s]"
    except FileNotFoundError:
        return "[OpenClaw error: openclaw CLI not found in PATH]"
    except Exception as e:
        return f"[OpenClaw error: {str(e)}]"


async def call_horizon(system: str, messages: list[dict]) -> str:
    if not TAVILY_API_KEY:
        return "[Horizon error: TAVILY_API_KEY not found in environment]"
    user_content = ""
    for msg in messages:
        if msg["role"] == "user":
            user_content = msg["content"]
            break
    search_query = user_content[:400].strip()
    url = "https://api.tavily.com/search"
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": search_query,
        "search_depth": "basic",
        "max_results": 5,
        "include_answer": True,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
        answer = data.get("answer", "")
        results = data.get("results", [])
        lines = ["[HORIZON — Live Web Research]"]
        if answer:
            lines.append(f"Summary: {answer}")
        for i, result in enumerate(results[:5], 1):
            title = result.get("title", "")
            content = result.get("content", "")[:200]
            url_str = result.get("url", "")
            lines.append(f"{i}. {title}\n   {content}\n   Source: {url_str}")
        return "\n\n".join(lines)
    except httpx.ConnectError:
        return "[Horizon error: Could not reach Tavily API]"
    except Exception as e:
        return f"[Horizon error: {str(e)}]"

# ─────────────────────────────────────────────
# WEB-ENABLED CALLERS (#web prefix activates these)
# Each model uses its own native search — not Tavily.
# GEP: Google Search grounding
# GRP: xAI Responses API with web_search tool
# Claude: Anthropic web_search tool
# Gemma: no search (local only), falls back to standard call_gemma
# ─────────────────────────────────────────────

async def call_gep_web(system: str, messages: list[dict], dynamic_system: str = "") -> str:
    """GEP with Google Search grounding. Static-first payload for CachedContent parity."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    augmented = _inject_dynamic_to_user(messages, dynamic_system)
    user_content = next((m["content"] for m in augmented if m["role"] == "user"), "")
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "tools": [{"google_search": {}}]
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
            meta = data.get("usageMetadata", {})
            _log_usage(
                provider="google",
                model=GEMINI_MODEL,
                input_tokens=meta.get("promptTokenCount", 0),
                output_tokens=meta.get("candidatesTokenCount", 0),
            )
            return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        logger.warning(f"GEP web search failed, falling back to standard: {e}")
        return await call_gep(system, messages, dynamic_system)

async def call_grp_web(system: str, messages: list[dict], dynamic_system: str = "") -> str:
    """GRP with native xAI web search via Responses API. Static-first for prefix cache."""
    augmented = _inject_dynamic_to_user(messages, dynamic_system)
    user_content = next((m["content"] for m in augmented if m["role"] == "user"), "")
    url = "https://api.x.ai/v1/responses"
    grp_web_model = os.getenv("GR_PRO_MODEL", "grok-4.3")
    payload = {
        "model": grp_web_model,
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content}
        ],
        "tools": [{"type": "web_search"}]
    }
    grp_web_headers = {"Authorization": f"Bearer {GROK_API_KEY}", "x-grok-conv-id": "peakforge-grp-v1"}
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post(url, json=payload, headers=grp_web_headers)
            r.raise_for_status()
            data = r.json()
            usage = data.get("usage", {})
            _log_usage(
                provider="xai",
                model=grp_web_model,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            )
            output = data.get("output", [])
            for item in reversed(output):
                if item.get("type") == "message":
                    content = item.get("content", [])
                    for part in content:
                        if part.get("type") == "output_text":
                            return part.get("text", "")
            if "choices" in data:
                return data["choices"][0]["message"]["content"]
            return "[GRP web: unexpected response format]"
    except Exception as e:
        logger.warning(f"GRP web search failed, falling back to standard: {e}")
        return await call_grp(system, messages, dynamic_system)

async def call_claude_web(system: str, messages: list[dict], dynamic_system: str = "") -> str:
    """Claude with native Anthropic web search tool. Accepts same two-block args as call_claude."""
    if not ANTHROPIC_API_KEY:
        return "[Claude error: ANTHROPIC_API_KEY not found in environment]"
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "prompt-caching-2024-07-31,web-search-2025-03-05",
        "content-type": "application/json"
    }
    user_content = ""
    for msg in messages:
        if msg["role"] == "user":
            user_content = msg["content"]
            break
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    if dynamic_system:
        system_blocks.append({"type": "text", "text": dynamic_system})
    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 4000,
        "system": system_blocks,
        "messages": [{"role": "user", "content": user_content}],
        "tools": [{"type": "web_search_20250305", "name": "web_search"}]
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            usage = data.get("usage", {})
            _log_usage(
                provider="anthropic",
                model="claude-sonnet-4-6",
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
                cache_read_tokens=usage.get("cache_read_input_tokens", 0),
            )
            # Extract text from content blocks (may include tool_use and tool_result blocks)
            text_parts = []
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text_parts.append(block["text"])
            return "\n".join(text_parts) if text_parts else "[Claude web: no text in response]"
    except Exception as e:
        logger.warning(f"Claude web search failed, falling back to standard: {e}")
        return await call_claude(system, messages)

# ─────────────────────────────────────────────
# AGENT CALLER MAPS
# ─────────────────────────────────────────────

AGENT_CALLERS = {
    "gec":      call_gep,
    "grc":      call_grp,
    "gef":      call_gec_flash,
    "grf":      call_grf,
    "nexus":    call_gep,
    "apex":     call_grp,
    "clc":      call_claude,
    "deepseek": call_deepseek,
    "gemma":    call_gemma,
    "horizon":  call_horizon,
    "openclaw": call_openclaw,
}

# Web-enabled callers — used when message starts with #web
# Agents without native search fall back to their standard caller
AGENT_CALLERS_WEB = {
    "gec":      call_gep_web,
    "grc":      call_grp_web,
    "nexus":    call_gep_web,   # Nexus uses GEP's caller
    "apex":     call_grp_web,   # Apex uses GRP's caller
    "clc":   call_claude_web,
    "deepseek": call_deepseek,  # DeepSeek has no native search
    "gemma":    call_gemma,     # Gemma is local-only, no search
    "horizon":  call_horizon,   # Horizon is always Tavily
    "openclaw": call_openclaw,  # OpenClaw is local — no web search variant needed
}

# ─────────────────────────────────────────────
# SLASH COMMAND HANDLERS
# ─────────────────────────────────────────────

async def handle_task_command(description: str) -> str:
    if not EMPIRE_STATE_PATH.exists():
        return "empire_state.json not found. Daemon may be offline."
    
    import time as _time
    thread_id = f"brain_{int(_time.time())}"
    
    state = json.loads(EMPIRE_STATE_PATH.read_text())
    state.setdefault("active_threads", []).append({
        "id": thread_id,
        "topic": description,
        "task": description,
        "assigned_to": "both",
        "status": "open",
        "timestamp": _time.time()
    })
    EMPIRE_STATE_PATH.write_text(json.dumps(state, indent=2))
    
    def _extract_text(val):
        if isinstance(val, dict):
            return val.get("analysis") or val.get("result") or str(val)
        return str(val) if val else ""
    
    await __import__("asyncio").sleep(3)
    
    for _ in range(24):
        await __import__("asyncio").sleep(5)
        state = json.loads(EMPIRE_STATE_PATH.read_text())
        threads = state.get("active_threads", [])
        
        task_thread = next((t for t in threads if t.get("id") == thread_id), None)
        
        gep_analysis = ""
        grp_analysis = ""
        gep_result = None
        grp_result = None
        task_completed_with_result = False
        
        if task_thread and task_thread.get("status") == "completed" and "result" in task_thread:
            result = task_thread.get("result", {})
            if isinstance(result, dict):
                gep_val = result.get("gec")
                grp_val = result.get("grc")
                if gep_val is not None or grp_val is not None:
                    task_completed_with_result = True
                    gep_analysis = _extract_text(gep_val) if gep_val is not None else ""
                    grp_analysis = _extract_text(grp_val) if grp_val is not None else ""
        
        for t in threads:
            if t.get("status") == "completed":
                agent_val = t.get("agent", "")
                assigned_val = t.get("assigned_to", "")
                if agent_val.upper() == "GEC" or assigned_val.upper() == "GEC":
                    gep_result = t
                elif agent_val.upper() == "GRC" or assigned_val.upper() == "GRC":
                    grp_result = t
        
        if gep_result:
            if not task_completed_with_result or (task_completed_with_result and not gep_analysis):
                gep_analysis = _extract_text(gep_result.get('analysis'))
        if grp_result:
            if not task_completed_with_result or (task_completed_with_result and not grp_analysis):
                grp_analysis = _extract_text(grp_result.get('analysis'))
        
        if task_completed_with_result or gep_result or grp_result:
            claude_system = AGENT_PERSONAS["clc"]
            claude_user_message = f"The PydanticAI daemon has returned these council responses to the task: '{description}'\n\nGEP: {gep_analysis}\n\nGRP: {grp_analysis}\n\nSynthesize into a single executive brief. Lead with the decision or recommendation. Maximum 150 words."
            claude_synthesis = await call_claude(claude_system, [{"role": "user", "content": claude_user_message}])
            
            if task_completed_with_result:
                for t in state.get("active_threads", []):
                    if t.get("id") == thread_id:
                        t["status"] = "completed"
                EMPIRE_STATE_PATH.write_text(json.dumps(state, indent=2))
            
            gep_raw = f"GEP: {gep_analysis}" if gep_analysis else "[GEP: Did not respond in time]"
            grp_raw = f"GRP: {grp_analysis}" if grp_analysis else "[GRP: No response]"
            
            return f"[TASK COMPLETE]\n\n[CLAUDE SYNTHESIS]\n{claude_synthesis}\n\n---\n[GEP RAW]\n{gep_raw}\n\n[GRP RAW]\n{grp_raw}"
    
    return "[TASK TIMEOUT] Daemon did not respond within 120s. Check /tmp/daemon.log"

async def handle_ledger_command(committee: str, recent_messages: list[dict], full_day_messages: list[dict] = None) -> str:
    block = "\n".join(f"[{m['speaker']}]: {m['content']}" for m in recent_messages)
    draft_prompt = f"Summarize the key decisions, pivots, architectural changes, and open questions from this council session in 3-7 bullet points for the COUNCIL_LEDGER. Be specific — include phase numbers, file names, and outcomes where relevant:\n\n{block}"
    system = AGENT_PERSONAS["gec"]
    gep_draft = await call_gep(system, [{"role": "user", "content": draft_prompt}])

    transcript_source = full_day_messages if (full_day_messages and len(full_day_messages) > 0) else recent_messages
    transcript_label = "full day" if (full_day_messages and len(full_day_messages) > 0) else "recent messages (full day unavailable — possible next-day ledger run)"

    if transcript_source and len(transcript_source) > 0:
        full_transcript = "\n".join(
            f"[{m['speaker']}]: {m['content'][:300]}"
            for m in transcript_source
            if m['speaker'] not in ('SYSTEM',)
        )
        if len(full_transcript) > 8000:
            full_transcript = full_transcript[-8000:]

        review_prompt = f"""GEP has drafted this ledger entry for today's council session:

---
{gep_draft}
---

Here is the session transcript ({transcript_label}):

{full_transcript}

Cross-check the draft against the full transcript. Return EXACTLY this format with no other text:

CONFIDENCE: HIGH | MEDIUM | LOW

GAPS:
- [list any significant decisions, code shipped, or architectural changes mentioned in the transcript that GEP missed — be specific with phase numbers and file names]
- [or write "None — all major items captured" if nothing is missing]

FLAGS:
- [list anything in GEP's draft that appears inaccurate or overstated based on the transcript]
- [or write "None" if draft is accurate]

REVIEWED DRAFT:
[Paste GEP's draft here with any corrections applied. If no corrections needed, paste it unchanged.]"""

        claude_system = AGENT_PERSONAS["clc"]
        claude_review = await call_claude(
            claude_system,
            [{"role": "user", "content": review_prompt}]
        )

        confidence = "MEDIUM"
        if "CONFIDENCE: HIGH" in claude_review.upper():
            confidence = "HIGH"
        elif "CONFIDENCE: LOW" in claude_review.upper():
            confidence = "LOW"

        confidence_emoji = {"HIGH": "✅", "MEDIUM": "⚠️", "LOW": "🔴"}.get(confidence, "⚠️")

        return f"""[LEDGER DRAFT — CLAUDE REVIEWED]
Confidence: {confidence_emoji} {confidence}
Source: {transcript_label}

{claude_review}"""

    else:
        return f"""[LEDGER DRAFT — GEP ONLY]
Confidence: ⚠️ MEDIUM (full day transcript unavailable)

GAPS:
- Unable to cross-check — full day transcript not provided

REVIEWED DRAFT:
{gep_draft}"""

async def handle_decision_command(committee: str, recent_messages: list[dict]) -> str:
    ledger = LEDGER_PATH.read_text(encoding="utf-8") if LEDGER_PATH.exists() else ""
    block = "\n".join(f"[{m['speaker']}]: {m['content']}" for m in recent_messages[-30:])
    prompt = f"Before we commit to the following decision, identify any gaps, contradictions with prior decisions, or missing context:\n\nLEDGER:\n{ledger}\n\nRECENT:\n{block}"
    system = AGENT_PERSONAS["gec"]
    
    import asyncio
    gep_task = call_gep(system, [{"role": "user", "content": prompt}])
    grp_task = call_grp(system, [{"role": "user", "content": prompt}])
    gep_result, grp_result = await asyncio.gather(gep_task, grp_task)
    
    claude_system = AGENT_PERSONAS["clc"]
    claude_user_message = f"GEP has provided this decision gate analysis:\n\n{gep_result}\n\nGRP has provided this decision gate analysis:\n\n{grp_result}\n\nAs CTO, identify any technical gaps, infrastructure risks, or implementation blind spots either advisor may have missed. Be brief."
    claude_result = await call_claude(claude_system, [{"role": "user", "content": claude_user_message}])
    
    return f"[GEP DECISION GATE]\n{gep_result}\n\n[GRP DECISION GATE]\n{grp_result}\n\n[CLAUDE — TECHNICAL REVIEW]\n{claude_result}"

async def handle_summary_command(committee: str, recent_messages: list[dict]) -> str:
    block = "\n".join(f"[{m['speaker']}]: {m['content']}" for m in recent_messages[-20:])
    prompt = f"Draft a 3-5 bullet summary of this {committee} committee session for cross-posting to the main Council:"
    system = AGENT_PERSONAS["gec"]
    return await call_gep(system, [{"role": "user", "content": f"{prompt}\n\n{block}"}])

WIKI_PATH = EMPIREWIKI_RAW_DIR.parent
WIKI_INDEX = WIKI_PATH / "wiki" / "index.md"
WIKI_LOG   = WIKI_PATH / "wiki" / "log.md"
WIKI_RAW   = WIKI_PATH / "raw"

async def handle_wiki_command(topic: str = "") -> str:
    WIKI_PATH.mkdir(parents=True, exist_ok=True)
    (WIKI_PATH / "wiki").mkdir(exist_ok=True)
    WIKI_RAW.mkdir(exist_ok=True)

    if topic:
        if not WIKI_INDEX.exists():
            return "[Wiki] No compiled wiki found. Run /wiki without arguments to compile first."
        index_content = WIKI_INDEX.read_text(encoding="utf-8")
        prompt = f"Search the Empire Wiki index for information about: {topic}\n\nWIKI INDEX:\n{index_content}\n\nSummarize all relevant entries. If nothing found, say so directly."
        system = "You are Horizon, the Empire Wiki curator. Answer questions from the compiled wiki index. Be precise and cite page references where possible."
        return await call_gep(system, [{"role": "user", "content": prompt}])

    else:
        raw_files = list(WIKI_RAW.glob("*.md")) + list(WIKI_RAW.glob("*.txt"))
        if not raw_files:
            return f"[Wiki] No source files found in {WIKI_RAW}. Drop .md or .txt files there to compile.\n\nTo add sources: cp ~/Library/Mobile\\ Documents/com~apple~CloudDocs/PeakForge-Shared/EOD_*.md {WIKI_RAW}/"

        raw_content = ""
        file_list = []
        for f in sorted(raw_files)[-20:]:
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                raw_content += f"\n\n## SOURCE: {f.name}\n{content[:3000]}"
                file_list.append(f.name)
            except Exception:
                continue

        if not raw_content:
            return "[Wiki] Could not read source files."

        compile_prompt = f"""You are Horizon, the Empire Wiki curator for PeakForge Empire.

Compile the following source documents into a structured wiki index.

Output a markdown document with:
1. ## EMPIRE WIKI INDEX — [today's date]
2. One section per major topic found across all sources
3. Each section: topic name, 2-3 sentence summary, key decisions/facts, source file references
4. At the end: ## OPEN THREADS — list any unresolved items or open questions found

Sources being compiled ({len(file_list)} files):
{chr(10).join(f'- {f}' for f in file_list)}

SOURCE CONTENT:
{raw_content}"""

        system = "You are Horizon, the Empire Wiki curator. Compile source documents into a clean, structured wiki index. Be factual and precise. No hallucination — only summarize what is explicitly in the sources."
        compiled = await call_gep(system, [{"role": "user", "content": compile_prompt}])

        from datetime import datetime
        from zoneinfo import ZoneInfo
        hst = ZoneInfo("Pacific/Honolulu")
        ts = datetime.now(hst).strftime("%Y-%m-%d %H:%M HST")
        WIKI_INDEX.write_text(compiled, encoding="utf-8")

        log_entry = f"## [{ts}] compile | {len(file_list)} files | {len(raw_files)} total in raw/\n"
        with open(WIKI_LOG, "a", encoding="utf-8") as lf:
            lf.write(log_entry)

        return f"[Wiki compiled — {len(file_list)} sources]\n\n{compiled[:2000]}{'...(truncated)' if len(compiled) > 2000 else ''}"

# ─────────────────────────────────────────────
# MAIN ROUTER
# ─────────────────────────────────────────────

async def route(query: str, committee: str, injected_file: str = "") -> dict[str, str]:
    import asyncio
    global AUTONOMOUS_STOP_FLAG

    # ── /approve <text> — ratify boardroom decision to COUNCIL_LEDGER.md ─
    if query.strip().lower().startswith("/approve "):
        decision_text = query.strip()[len("/approve "):].strip()
        if decision_text:
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M HST")
            entry = f"\n## {timestamp} — Boardroom Ratification\n\n{decision_text}\n"
            with open(LEDGER_PATH, "a", encoding="utf-8") as f:
                f.write(entry)
            return {"system": f"✅ Decision ratified and appended to COUNCIL_LEDGER.md ({timestamp})"}
        return {"system": "[/approve] No decision text provided."}

    # ── /approve-research — commit pending research findings ──────────
    if query.strip().lower() == "/approve-research":
        return {"system": await handle_approve_research_command()}

    # ── /approve-ideas — show pending EMPIRE_IDEAS changes ────────────
    if query.strip().lower() == "/approve-ideas":
        return {"system": await handle_approve_ideas_command()}

    # ── /stop — halt active autonomous loop ───────────────────────────
    if query.strip().lower() == "/stop":
        AUTONOMOUS_STOP_FLAG = True
        return {"system": "[STOP signal sent — autonomous loop will halt after current step.]"}

    # ── /reflect — Experiential Reflection Layer (Phase 13.1) ──────────
    if query.strip().lower().startswith("/reflect"):
        topic = query.strip()[8:].strip()
        return {"system": await handle_reflect_command(topic, committee)}

    # ── /plan — PydanticCouncil round-trip (CL → GEMP + GRM → CL synth) ─
    if query.strip().lower().startswith("/plan"):
        prompt = query.strip()[5:].strip()
        if not prompt:
            return {"system": (
                "[/plan] Usage: /plan [architecture or strategy question]\n"
                "Routes to PydanticCouncil autonomous round-trip "
                "(CL drafts role-tagged ask → GEMP + GRM respond in parallel → CL synthesizes). "
                "Latency targets: ≤75s cold, ≤45s warm. "
                "See PydanticCouncil/DEFAULT_SURFACE_SAFETY.md for integrity + kill-switch."
            )}
        return {"clc": await handle_plan_command(prompt)}

    # ── #fix prefix — Council-Certified Autonomous Fix (Tech only) ─────
    if query.strip().lower().startswith("#fix") and committee == "tech":
        description = query.strip()[4:].strip()
        if description:
            logger.info(f"[#fix] Autonomous fix triggered: {description[:80]}")
            result = await handle_autonomous_fix(description)
            return {"clc": result}
        else:
            return {"clc": "Usage: #fix [describe the problem in plain English]"}

    # ── #web prefix detection ──────────────────────────────────────────
    web_mode = False
    if query.strip().lower().startswith("#web"):
        web_mode = True
        query = query.strip()[4:].strip()
        logger.info(f"#web mode activated for committee: {committee}")

    # ── Autonomous file intent detection (Phase 11.9a) ─────────────────
    # If query implies a file read and no file is already injected,
    # auto-fetch the file via cl_bridge and prepend to context.
    # Tier 0 — read-only, no approval required.
    if not injected_file:
        # Scan only the first 500 chars (user's original prompt) — not history.
        # Debate history embeds agent responses that cite canonical file paths;
        # scanning the full query causes detect_file_intent to auto-load the
        # canonical spec (~35K tokens) into every round-2+ boardroom call.
        intent_query = query.split("\n\nPREVIOUS DEBATE ROUNDS")[0][:500]
        detected_path = await detect_file_intent(intent_query)
        if detected_path:
            logger.info(f"File intent detected: {detected_path}")
            file_content = await handle_file_command(detected_path)
            injected_file = file_content

    # ── @mention parsing ───────────────────────────────────────────────
    targets, cleaned_query = parse_mentions(query)

    if targets:
        logger.info(f"@mentions detected: {targets} (committee: {committee})")
        query_for_context = cleaned_query
    else:
        query_for_context = query

    full_query = f"{query_for_context}\n\n[ATTACHED FILE]\n{injected_file}" if injected_file else query_for_context
    # T3 fix (2026-04-30): split static (cached) from dynamic (per-call) context.
    # Claude callers receive both blocks separately; non-Claude callers get a flat join.
    static_ctx, dynamic_ctx = build_context_split(full_query, committee)
    messages = [{"role": "user", "content": full_query}]
    results = {}

    # ── Determine agents ───────────────────────────────────────────────
    if targets:
        agents = targets
        logger.info(f"Fan-out routing to: {agents}")
    else:
        agents = COMMITTEE_ROUTING.get(committee, ["gec", "grc"])
        logger.info(f"Committee routing: {agents}")

    # ── Per-persona caller dispatch (canonical §14.0 T1) ───────────────
    # Anthropic CLAUDE + Gemini GEP/NEXUS: web_search rides on the same endpoint
    # (/v1/messages and generateContent respectively) — always-web is free.
    # xAI GRP/APEX: web_search requires Responses API (/v1/responses) per xAI
    # docs as of Jan 2026 (Chat Completions Live Search retired 2026-01-12).
    # Responses API has higher baseline latency + the configured reasoning model
    # adds chain-of-thought wall-time → heuristic-dispatch GRP/APEX to Chat
    # Completions (fast) for non-web queries. Explicit #web forces web for all.
    XAI_PERSONAS = {"grc", "apex"}
    WEB_TRIGGER_KEYWORDS = (
        "latest", "today", "currently", "current ", "right now", "as of",
        "recent", "this week", "this month", "this year", "yesterday",
        "price", "news", "announcement", "happening", "released",
        "launched", "published", "stock", "weather",
    )
    _query_lc = query.lower()
    query_needs_web = any(kw in _query_lc for kw in WEB_TRIGGER_KEYWORDS)

    def _select_caller(agent: str):
        # Flash tier: gec on low-stakes committees (health/chronos/marketing/secops)
        if agent == "gec" and committee in FLASH_COMMITTEES and not web_mode:
            return call_gec_flash
        if web_mode:
            return AGENT_CALLERS_WEB.get(agent, AGENT_CALLERS[agent])
        if (agent in XAI_PERSONAS or agent == "gec") and not query_needs_web:
            return AGENT_CALLERS[agent]
        return AGENT_CALLERS_WEB.get(agent, AGENT_CALLERS[agent])

    async def call_agent(agent):
        committee_focus = COMMITTEE_CONTEXT.get(committee, "")
        # Tech and Trading committees require explicit reasoning before responding.
        # Other committees use standard system prompt construction.
        if committee in ("tech", "trading"):
            thinking_directive = (
                "\n\nMANDATORY REASONING PROTOCOL: Before responding to any request in this tab, "
                "you MUST open with a <thinking> block. Inside this block, reason through: "
                "(1) what the user is actually asking for, "
                "(2) what files or system state are relevant, "
                "(3) what the precise next action or directive should be, "
                "(4) whether this is an @openclaw immediate task or a /cascade async task. "
                "Only after completing your <thinking> block should you write your response. "
                "Your <thinking> block is for your own reasoning only. The UI strips it before "
                "rendering to the CEO — so make it real reasoning, not ritual. "
                "Strip all macro-governance preamble from your response. "
                "Do not recite empire laws or permanent rules. Get to the technical substance immediately."
            )
            # Law #35 — Cross-Modal Review enforcement (Phase 14)
            # Backtest or Dead Drop results in Trading require realism check before any execution command
            _backtest_kw = ("backtest", "dead drop", "factory", "win rate", "profit factor",
                            "win%", "pnl", "equity curve", "trades_v", "bot16")
            if committee == "trading" and any(kw in query.lower() for kw in _backtest_kw):
                thinking_directive += (
                    "\n\nLAW #35 — CROSS-MODAL REVIEW REQUIRED: This message contains backtest "
                    "or Dead Drop results. Before issuing ANY execution command you MUST complete "
                    "this realism check DIRECTLY IN YOUR RESPONSE BODY (not in thinking tags): "
                    "[Empire History Check] Have we seen unrealistically high win-rates or PF before? "
                    "Yes — v1 baseline was 38% win rate, PF 0.21, net -$75,975. "
                    "[Consequence] Acting on inflated numbers without this check leads to live capital loss. "
                    "[Heuristic] Win rate >65% or PF >2.0 on a supposed baseline = look-ahead bias flag. "
                    "Stop. Analyze before cascading. "
                    "[Answer] State your explicit realism verdict BEFORE recommending any next action. "
                    "Do not issue @openclaw or /cascade until verdict is stated."
                )
        else:
            thinking_directive = ""
        # Static system block: persona + mandates + file-based context.
        # This is what Anthropic caches — must be identical across calls in a session.
        static_system = (
            f"{AGENT_PERSONAS.get(agent, AGENT_PERSONAS['gec'])}"
            f"{thinking_directive}"
            f"{READ_FILE_DIRECTIVE}"
            f"\n\n{f'COMMITTEE MANDATE: {committee_focus}' if committee_focus else ''}"
            f"\n\n{static_ctx}"
        )
        caller = _select_caller(agent)
        # T3 fix (2026-04-30): all cache-aware callers accept (system, messages, dynamic_system).
        # Claude: dynamic goes in a second system block (no cache_control).
        # Grok/Gemini: dynamic is prepended to user message via _inject_dynamic_to_user(),
        #   keeping system message = static bedrock → prefix cache hits on x-grok-conv-id.
        # Uncached callers (deepseek, gemma, openclaw, horizon): fall back to flat concat.
        DYNAMIC_AWARE = {"clc", "gec", "nexus", "grc", "apex", "gef", "grf"}
        if agent in DYNAMIC_AWARE:
            response = await caller(static_system, messages, dynamic_ctx)
        else:
            flat_system = static_system + ("\n\n" + dynamic_ctx if dynamic_ctx else "")
            response = await caller(flat_system, messages)
        # G-L4-D: if model requested a file read, fulfill it and re-call once.
        m = READ_FILE_RE.search(response)
        if m:
            file_path = m.group(1).strip()
            file_content = await _cl_read_file(file_path)
            augmented = messages + [
                {"role": "assistant", "content": response},
                {"role": "user", "content": f"[FILE: {file_path}]\n{file_content}\n\nNow complete your response with this file in context."}
            ]
            if agent in DYNAMIC_AWARE:
                response = await caller(static_system, augmented, dynamic_ctx)
            else:
                response = await caller(flat_system, augmented)
        results[agent] = response

    await asyncio.gather(*[call_agent(a) for a in agents])
    # Strip <thinking> blocks — keep internal reasoning off the UI
    import re as _re
    _think = _re.compile(r'<thinking>.*?</thinking>', _re.DOTALL | _re.IGNORECASE)
    for k in list(results.keys()):
        results[k] = _think.sub('', results[k]).strip()
    return results

# ─────────────────────────────────────────────
# PHASE 11.9a — AGENTIC TOOL HANDLERS
# ─────────────────────────────────────────────

CL_BRIDGE_URL = "http://127.0.0.1:8511"
CL_BRIDGE_TOKEN = os.getenv("CL_BRIDGE_TOKEN", "pf-mcp-2026")

# EMPIRE_TASKS_PATH imported from config

async def handle_file_command(file_path: str) -> str:
    """
    /file [path] — Read any file on PF via cl_bridge and inject content.
    Tier 0 — read-only, no approval required.
    Models receive the file content and can reason over real code.
    """
    if not file_path:
        return "[/file] Usage: /file /path/to/file"

    # Safety: only allow reads under configured FILE_ACCESS_PREFIX
    if not file_path.startswith(FILE_ACCESS_PREFIX):
        return f"[/file] Access denied — path must be under {FILE_ACCESS_PREFIX}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{CL_BRIDGE_URL}/read_file",
                headers={"Authorization": f"Bearer {CL_BRIDGE_TOKEN}"},
                params={"path": file_path}
            )
            if r.status_code == 200:
                data = r.json()
                content = data.get("content", "")
                lines = content.count("\n") + 1
                return f"[FILE: {file_path}] ({lines} lines)\n\n{content}"
            else:
                return f"[/file] cl_bridge error {r.status_code}: {r.text[:200]}"
    except httpx.ConnectError:
        return "[/file] cl_bridge not reachable — is it running on :8511?"
    except Exception as e:
        return f"[/file] Error: {str(e)}"


async def handle_reflect_command(topic: str, committee: str) -> str:
    """/reflect [topic] — Pull recent EODs + EMPIRE_RESEARCH, generate heuristics via GEP+GRP reflection."""
    import asyncio
    import glob
    from pathlib import Path
    
    eod_dir = PF_SHARED_DIR
    research_path = EMPIRE_RESEARCH_PATH
    
    # Gather last 4 EOD files
    eod_block = ""
    if eod_dir.exists():
        eods = sorted(eod_dir.glob("EOD_CONTINUITY_*.md"), reverse=True)[:4]
        for eod in eods:
            try:
                content = eod.read_text(encoding="utf-8")
                eod_block += f"\n--- {eod.name} ---\n{content[:1500]}\n"
            except Exception:
                pass
    
    # Gather EMPIRE_RESEARCH
    research_block = ""
    if research_path.exists():
        try:
            research_block = research_path.read_text(encoding="utf-8")[:2000]
        except Exception:
            pass
    
    if not eod_block and not research_block:
        return "[/reflect] No EOD or EMPIRE_RESEARCH content found yet. Run after a few sessions."
    
    reflect_prompt = f"""You are reflecting on PeakForge Empire's lived experience to generate actionable heuristics.

TOPIC: {topic if topic else "general empire operations"}

RECENT EODs (last 4 sessions):
{eod_block or "[none found]"}

EMPIRE RESEARCH LEDGER:
{research_block or "[none found]"}

Generate 3-5 When-Then heuristics based on this lived experience:
1. [Empire History Check] — What similar situations have we encountered before?
2. [Consequence] — What were the outcomes (good/bad) of those past decisions?
3. [Heuristic] — What rule or pattern emerges from this experience?
4. [Answer] — What specific action should we take now given this topic?

Format each heuristic as:
- **When**: [situation pattern]
- **Then**: [action or decision]
- **Rationale**: [why this works based on our history]
- **Confidence**: [High/Medium/Low based on evidence strength]

Focus on practical, executable guidance for the {committee} committee."""
    
    # Call GEP and GRP in parallel
    try:
        gep_result, grp_result = await asyncio.gather(
            call_gep("You are GEP, reflecting on PeakForge Empire's lived experience to generate actionable heuristics.", [{"role": "user", "content": reflect_prompt}]),
            call_grp("You are GRP, reflecting on PeakForge Empire's lived experience to generate actionable heuristics.", [{"role": "user", "content": reflect_prompt}])
        )
        
        combined = f"""## Experiential Reflection Layer — {topic if topic else "General"}
        
**GEP (Strategic Analysis):**
{gep_result[:1500]}

**GRP (Practical Implementation):**
{grp_result[:1500]}

**Synthesized Heuristics:**
1. Prioritize actions with clear historical precedent and positive outcomes.
2. Avoid patterns that previously led to negative consequences.
3. When uncertain, test small before scaling based on similar past experiments."""
        
        return f"[/reflect] Reflection complete:\n\n{combined}"
        
    except Exception as e:
        return f"[/reflect] Error during reflection: {str(e)}"


async def handle_approve_research_command() -> str:
    """/approve-research — commit EMPIRE_RESEARCH_PENDING.md to EMPIRE_RESEARCH.md"""
    pending = EMPIRE_RESEARCH_PENDING
    ledger = EMPIRE_RESEARCH_PATH
    if not pending.exists():
        return "[/approve-research] No pending research found. Run Horizon or wait for tomorrow's 06:15 brief."
    pending_content = pending.read_text(encoding="utf-8")
    existing = ledger.read_text(encoding="utf-8") if ledger.exists() else ""
    # Append pending findings after the last entry
    separator = "\n\n---\n\n"
    new_content = existing.rstrip() + separator + pending_content.strip()
    ledger.write_text(new_content, encoding="utf-8")
    pending.unlink()
    return f"✓ Research findings committed to EMPIRE_RESEARCH.md ({len(pending_content)} chars appended). Pending file cleared."


async def handle_approve_ideas_command() -> str:
    """/approve-ideas — show pending EMPIRE_IDEAS changes for CEO review"""
    pending = EMPIRE_IDEAS_PENDING
    if not pending.exists():
        return "[/approve-ideas] No pending ideas review found. Runs automatically every Sunday at 22:00 HST."
    content = pending.read_text(encoding="utf-8")
    pending.unlink()
    return f"**EMPIRE_IDEAS Proposed Changes (Sunday Review):**\n\n{content}\n\n*Review above and apply changes manually to EMPIRE_IDEAS.md, or discard.*"


async def handle_history_command(committee: str, n: int = 30) -> str:
    """
    /history [committee] [n] — Pull SQLite history and inject into conversation.
    Lets models and Dalen reference past committee discussions in current context.
    """
    from db import load_recent
    try:
        messages = load_recent(committee, n=n)
        if not messages:
            return f"[/history] No messages found for committee: {committee}"

        from datetime import datetime
        lines = [f"[HISTORY — {committee.upper()} — last {len(messages)} messages]"]
        for m in messages:
            try:
                ts = datetime.fromtimestamp(float(m["timestamp"])).strftime("%Y-%m-%d %H:%M")
            except Exception:
                ts = "unknown"
            lines.append(f"\n[{ts}] {m['speaker']}: {m['content'][:500]}")

        return "\n".join(lines)
    except Exception as e:
        return f"[/history] Error: {str(e)}"


async def handle_task_add_command(task_text: str) -> str:
    """
    /task-add [text] — Append a task to EMPIRE_TASKS.md directly from the UI.
    Eliminates need to go to claude.ai to update the build queue.
    """
    if not task_text:
        return "[/task-add] Usage: /task-add #I-XX: description"

    from datetime import datetime
    from zoneinfo import ZoneInfo
    hst = ZoneInfo("Pacific/Honolulu")
    ts = datetime.now(hst).strftime("%Y-%m-%d %H:%M HST")

    try:
        EMPIRE_TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Append to EMPIRE_TASKS.md
        entry = f"\n- {task_text}  _(added {ts})_"
        with open(EMPIRE_TASKS_PATH, "a", encoding="utf-8") as f:
            f.write(entry)

        return f"✓ Task added to EMPIRE_TASKS.md:\n{entry.strip()}\n\nRun /wiki to recompile wiki index."
    except Exception as e:
        return f"[/task-add] Error: {str(e)}"


async def handle_propose_command(command: str) -> str:
    """
    /propose [command] — Dalen explicitly approves and executes a proposed command.
    Called after a model surfaces a [PROPOSE: command] block.
    Routes to cl_bridge — Tier 1 auto-executes, Tier 2 requires cl_bridge approval flow.
    """
    if not command:
        return "[/propose] Usage: /propose [command to execute]"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Submit command to cl_bridge
            r = await client.post(
                f"{CL_BRIDGE_URL}/run_command",
                headers={
                    "Authorization": f"Bearer {CL_BRIDGE_TOKEN}",
                    "Content-Type": "application/json"
                },
                json={
                    "command": command,
                    "working_dir": str(_PF_ROOT / "StrategicBrain")
                }
            )
            data = r.json()

            if r.status_code == 200:
                stdout = data.get("stdout", "")
                stderr = data.get("stderr", "")
                output = stdout or stderr or "(no output)"
                return f"✓ Executed: {command}\n\nOutput:\n{output}"
            elif r.status_code == 202:
                # cl_bridge returns approval_id inside detail dict
                detail = data.get("detail", {})
                if isinstance(detail, str):
                    import json as _json
                    try: detail = _json.loads(detail)
                    except: detail = {}
                approval_id = detail.get("approval_id", "")
                return (
                    f"⏳ Tier 2 command requires approval.\n"
                    f"Approval ID: {approval_id}\n\n"
                    f"To approve, run:\n/propose-approve {approval_id} {command}"
                )
            elif r.status_code == 403:
                reason = data.get("error", "blocked")
                return f"🚫 Command blocked by cl_bridge: {reason}"
            else:
                return f"[/propose] cl_bridge returned {r.status_code}: {data}"
    except httpx.ConnectError:
        return "[/propose] cl_bridge not reachable — is it running on :8511?"
    except Exception as e:
        return f"[/propose] Error: {str(e)}"


async def detect_file_intent(query: str) -> str:
    """
    Detect if a query implies a file read is needed.
    Returns file path if detected, empty string otherwise.
    Used by route() for autonomous Tier 0 file injection.

    Patterns detected:
    - "in wiki_sync.sh" / "wiki_sync.sh timing"
    - "adjust X in /path/to/file"
    - "read /path/to/file"
    - "look at /path/to/file"
    - "open /Users/peakforge/..."
    """
    import re

    # Explicit absolute path
    _home = str(Path.home())
    path_match = re.search(
        r'(' + re.escape(_home) + r'/[\w/._-]+\.(?:py|sh|md|json|txt|log|html|yaml|yml))',
        query
    )
    if path_match:
        return path_match.group(1)

    # Known file names — adapt these paths to your deployment layout
    _brain = str(_PF_ROOT / "StrategicBrain")
    KNOWN_FILES = {
        "router.py":      f"{_brain}/router.py",
        "chat_server.py": f"{_brain}/chat_server.py",
        "context.py":     f"{_brain}/context.py",
        "db.py":          f"{_brain}/db.py",
        "index.html":     f"{_brain}/ui/index.html",
        "EMPIRE_TASKS.md": str(EMPIRE_TASKS_PATH),
        "EMPIRE_TASKS":   str(EMPIRE_TASKS_PATH),
        "empire tasks":   str(EMPIRE_TASKS_PATH),
        "COUNCIL_LEDGER.md": f"{_brain}/COUNCIL_LEDGER.md",
    }

    query_lower = query.lower()
    for filename, path in KNOWN_FILES.items():
        if filename.lower() in query_lower:
            # Only auto-read if query implies an action or read intent
            action_words = ["adjust", "change", "fix", "update", "modify", "read", "look at",
                          "show", "open", "check", "review", "timing", "color", "icon",
                          "interval", "edit", "see", "what is in", "what's in",
                          "post", "print", "list", "show me all", "display", "get",
                          "fetch", "dump", "cat", "give me", "share", "pull"]
            if any(word in query_lower for word in action_words):
                return path

    return ""


async def handle_propose_approve_command(approval_id: str, command: str) -> str:
    """
    /propose-approve [approval_id] [command]
    Approves a pending Tier 2 command and executes it via cl_bridge.
    cl_bridge handles approval inline in /run_command — no separate /approve endpoint needed.
    Re-submit the command with approved=True + approval_id directly.
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{CL_BRIDGE_URL}/run_command",
                headers={
                    "Authorization": f"Bearer {CL_BRIDGE_TOKEN}",
                    "Content-Type": "application/json"
                },
                json={
                    "command": command,
                    "working_dir": str(_PF_ROOT / "StrategicBrain"),
                    "approved": True,
                    "approval_id": approval_id
                }
            )
            data = r.json()
            if r.status_code == 200:
                stdout = data.get("stdout", "")
                stderr = data.get("stderr", "")
                output = stdout or stderr or "(no output)"
                return f"✓ Tier 2 approved and executed: {command}\n\nOutput:\n{output}"
            else:
                return f"[/propose-approve] Execution failed {r.status_code}: {data}"
    except httpx.ConnectError:
        return "[/propose-approve] cl_bridge not reachable — is it running on :8511?"
    except Exception as e:
        return f"[/propose-approve] Error: {str(e)}"


# ─────────────────────────────────────────────
# PHASE 11.9d — COUNCIL-CERTIFIED AUTONOMOUS FIX
# ─────────────────────────────────────────────
#
# Triggered by #fix [description] in the Tech committee.
# Flow:
#   1. Claude reads relevant files via cl_bridge
#   2. Claude drafts the fix as a python3 -c one-liner
#   3. GEP + GRP review in plain English and co-sign
#   4. [PROPOSE: command] auto-executes via cl_bridge
#   5. Claude verifies the fix worked
#   6. Plain-English report to Dalen
#
# Dalen is out of the loop until the final report.
# Max 5 iterations before escalation.
# Auto-backup fires before every file write.
# /stop cancels an active loop.
#

AUTONOMOUS_STOP_FLAG = False
from config import PF_ROOT as _PF_ROOT
BACKUP_DIR = _PF_ROOT / "Backups" / "Autonomous_Fixes"


async def _cl_read_file(path: str) -> str:
    """Read a file — direct filesystem first (router runs on host), cl_bridge fallback."""
    # Fast path: router.py has direct host filesystem access; no cl_bridge dependency.
    try:
        p = Path(path).resolve()
        if str(p).startswith(FILE_ACCESS_PREFIX) and p.exists():
            return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    # Fallback: cl_bridge (handles remote or non-standard paths)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{CL_BRIDGE_URL}/read_file",
                headers={"Authorization": f"Bearer {CL_BRIDGE_TOKEN}"},
                params={"path": path}
            )
            if r.status_code == 200:
                return r.json().get("content", "")
            return f"[read error {r.status_code}]"
    except Exception as e:
        return f"[read error: {e}]"


async def _cl_execute(command: str, working_dir: str = "") -> dict:
    if not working_dir:
        working_dir = str(_PF_ROOT / "StrategicBrain")
    """
    Execute a command via cl_bridge for the autonomous fix loop.
    Tier 1 (python3 -c): cl_bridge auto-approves — no approval_id needed.
    Tier 2: autonomous loop cannot self-approve — returns structured failure.
    """
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{CL_BRIDGE_URL}/run_command",
                headers={
                    "Authorization": f"Bearer {CL_BRIDGE_TOKEN}",
                    "Content-Type": "application/json"
                },
                json={
                    "command": command,
                    "working_dir": working_dir
                }
            )
            if r.status_code == 200:
                data = r.json()
                return {
                    "ok": True,
                    "stdout": data.get("stdout", ""),
                    "stderr": data.get("stderr", ""),
                    "returncode": data.get("returncode", 0)
                }
            elif r.status_code == 202:
                detail = r.json().get("detail", {})
                if isinstance(detail, str):
                    import json as _json
                    try: detail = _json.loads(detail)
                    except: detail = {}
                approval_id = detail.get("approval_id", "unknown")
                return {
                    "ok": False,
                    "error": f"Tier 2 approval required (id: {approval_id}). Autonomous loop cannot self-approve Tier 2 commands. Report to Dalen for manual execution."
                }
            elif r.status_code == 403:
                return {"ok": False, "error": f"cl_bridge blocked: {r.json().get('error', 'unknown')}"}
            else:
                return {"ok": False, "error": f"cl_bridge {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _auto_backup(file_path: str) -> str:
    """Auto-backup a file before modification. Returns backup path or error."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        hst = ZoneInfo("Pacific/Honolulu")
        ts = datetime.now(hst).strftime("%Y%m%d_%H%M%S")
        filename = Path(file_path).name
        backup_path = BACKUP_DIR / f"{filename}.{ts}.bak"
        content = await _cl_read_file(file_path)
        if not content.startswith("[read error"):
            backup_path.write_text(content, encoding="utf-8")
            return str(backup_path)
        return f"[backup failed: could not read {file_path}]"
    except Exception as e:
        return f"[backup failed: {e}]"


async def handle_autonomous_fix(description: str) -> str:
    """
    Phase 11.9d — Universal Constructor.
    Triggered by #fix [description] in Tech committee only.

    Full-file rewrite model: Claude reads files, generates complete corrected
    content, GEP+GRP review manifest in plain English, files written atomically,
    verified, Dalen gets plain-English report.

    Supports: patching existing files, creating new files, mkdir operations.
    Max 5 iterations. Auto-backup before every file write. /stop halts the loop.
    """
    global AUTONOMOUS_STOP_FLAG
    AUTONOMOUS_STOP_FLAG = False

    import json as _json
    import re as _re
    import asyncio as _asyncio

    logger.info(f"[AUTONOMOUS_FIX] Starting: {description[:80]}")

    # ── Step 1: Filesystem search — find files by name ────────────────
    #
    # Instead of asking Claude to guess paths from a lookup table,
    # we extract filenames from the description and use `find` on the
    # real filesystem. Fuzzy fallback handles typos (cl-bridge → cl_bridge.py).
    # Ecosphere paths prioritized on multi-match. 5-minute in-memory cache.
    #
    import re as _re
    import json as _json
    import asyncio as _asyncio
    import time as _time

    # ── 5-minute find cache ────────────────────────────────────────────
    _find_cache: dict = getattr(handle_autonomous_fix, "_find_cache", {})
    handle_autonomous_fix._find_cache = _find_cache

    async def _find_file(name: str) -> list[str]:
        """Find files by name on PF filesystem using python3 os.walk (Tier 1).
        Returns sorted list, Ecosphere first."""
        now = _time.time()
        cache_key = name.lower()
        if cache_key in _find_cache:
            cached_paths, cached_at = _find_cache[cache_key]
            if now - cached_at < 300:  # 5-minute TTL
                logger.info(f"[AUTONOMOUS_FIX] Cache hit: {name} → {cached_paths}")
                return cached_paths

        # Single-line os.walk search — no newlines, no quote mangling
        name_escaped = name.replace("'", "\\'")
        script = (
            f"import os; skip={{'.git','backups','Backups','__pycache__'}}; results=[]; "
            f"[(dirs.__setitem__(slice(None), [d for d in dirs if d not in skip]), "
            f"results.append(os.path.join(root, '{name_escaped}'))) "
            f"for root,dirs,files in os.walk({str(Path.home())!r}) if '{name_escaped}' in files]; "
            f"print('\\n'.join(results))"
        )
        find_result = await _cl_execute(
            f'python3 -c "{script}"',
            working_dir=str(_PF_ROOT)
        )
        raw = find_result.get("stdout", "").strip()
        stderr = find_result.get("stderr", "").strip()
        if not raw:
            logger.info(f"[AUTONOMOUS_FIX] os.walk found nothing for '{name}'. stderr: {stderr[:100]}")
            return []

        paths = [p.strip() for p in raw.splitlines() if p.strip()]

        # Ecosphere first, then Trading, then scripts, then rest
        def _priority(p: str) -> int:
            if "/PeakForge-Ecosphere/" in p: return 0
            if "/PeakForge-Trading/" in p: return 1
            if "/scripts/" in p: return 2
            return 3

        paths.sort(key=_priority)
        _find_cache[cache_key] = (paths, now)
        logger.info(f"[AUTONOMOUS_FIX] Found '{name}': {paths}")
        return paths

    async def _fuzzy_find(term: str) -> list[str]:
        """Fuzzy find: normalize term and try common variations."""
        # Normalize: replace hyphens with underscores, ensure extension
        normalized = term.replace("-", "_")
        candidates = [term, normalized]
        # Add .py and .sh extensions if no extension present
        if "." not in term:
            candidates += [term + ".py", term + ".sh", normalized + ".py", normalized + ".sh"]
        # Also try with .md
        candidates += [term + ".md", normalized + ".md"]

        for candidate in candidates:
            results = await _find_file(candidate)
            if results:
                return results
        return []

    # ── Extract filenames from description ─────────────────────────────
    # Match: word.ext patterns, or bare words that look like filenames
    filename_pattern = _re.compile(
        r'\b([\w\-]+\.(?:py|sh|md|json|txt|html|yaml|yml|log|js|css))\b'
        r'|'
        r'\b([\w\-]{4,}(?:\.py|\.sh)?)\b(?=\s+(?:file|script|module|function|handler))',
        _re.IGNORECASE
    )
    raw_matches = filename_pattern.findall(description)
    candidate_names = list(dict.fromkeys([
        (m[0] or m[1]).strip() for m in raw_matches if (m[0] or m[1]).strip()
    ]))[:3]

    logger.info(f"[AUTONOMOUS_FIX] Filename candidates from description: {candidate_names}")

    # ── Search filesystem for each candidate ──────────────────────────
    relevant_files = []
    fuzzy_suggestions = []

    for name in candidate_names:
        results = await _find_file(name)
        if results:
            relevant_files.append(results[0])  # Take Ecosphere-priority match
        else:
            # Try fuzzy
            fuzzy = await _fuzzy_find(name)
            if fuzzy:
                relevant_files.append(fuzzy[0])
                fuzzy_suggestions.append(f"'{name}' → found as '{fuzzy[0].split('/')[-1]}'")
            else:
                fuzzy_suggestions.append(f"'{name}' → not found on filesystem")

    relevant_files = list(dict.fromkeys(relevant_files))[:3]  # dedupe, max 3

    # ── Path safety gate — reject site-packages, venv, and non-peakforge paths ──
    # Prevents Aider from targeting library files instead of project files.
    APPROVED_PREFIXES = [
        str(_PF_ROOT) + "/",
        str(Path.home() / "scripts") + "/",
        str(Path.home() / ".openclaw" / "workspace") + "/",
    ]
    REJECTED_PATTERNS = ["site-packages", "miniforge3/lib", "miniforge3/pkgs", ".venv", "node_modules"]

    safe_files = []
    rejected_files = []
    for path in relevant_files:
        is_approved = any(path.startswith(prefix) for prefix in APPROVED_PREFIXES)
        is_rejected = any(pat in path for pat in REJECTED_PATTERNS)
        if is_approved and not is_rejected:
            safe_files.append(path)
        else:
            rejected_files.append(path)

    if rejected_files:
        logger.warning(f"[AUTONOMOUS_FIX] Path gate rejected: {rejected_files}")

    if not safe_files and not relevant_files:
        pass  # Let the existing empty-files gate below handle this
    elif not safe_files and relevant_files:
        # Found files but all were outside approved directories
        return (
            f"[#fix BLOCKED — unsafe paths]\n"
            f"Files found but outside approved directories:\n"
            + "\n".join(f"  - {p}" for p in rejected_files)
            + f"\n\nOnly files under PeakForge-Ecosphere/, PeakForge-Trading/, or scripts/ "
            f"can be edited autonomously.\n"
            f"Provide the full absolute path in your #fix description to resolve ambiguity."
        )

    relevant_files = safe_files

    # ── Ask Claude for new_files / new_dirs if needed ─────────────────
    # Only call Claude API if description implies creating something new
    new_files: list[str] = []
    new_dirs: list[str] = []

    create_keywords = ["create", "new file", "new script", "scaffold", "add a new", "make a new", "build a new"]
    if any(kw in description.lower() for kw in create_keywords):
        new_file_prompt = f"""You are CL, PeakForge Empire CTO. Dalen wants to:
"{description}"

Existing files found: {relevant_files}

What new files or directories need to be created? Respond with JSON only:
{{
  "new_files": ["/absolute/path/to/new_file.py"],
  "new_dirs": ["/absolute/path/to/new_dir/"]
}}

All paths must start with {FILE_ACCESS_PREFIX}/. Use empty arrays if nothing new needed."""

        try:
            nf_response = await call_deepseek(
                "You are CL, PeakForge CTO. JSON only. No markdown.",
                [{"role": "user", "content": new_file_prompt}]
            )
            nf_match = _re.search(r'\{.*\}', nf_response, _re.DOTALL)
            if nf_match:
                nf_data = _json.loads(nf_match.group())
                new_files = nf_data.get("new_files", [])
                new_dirs = nf_data.get("new_dirs", [])
        except Exception:
            pass

    # ── Fail with helpful message if nothing found ─────────────────────
    if not relevant_files and not new_files:
        suggestion = ""
        if fuzzy_suggestions:
            suggestion = f"\n\nSearch results:\n" + "\n".join(f"  - {s}" for s in fuzzy_suggestions)
        return (
            f"[#fix FAILED] Could not locate files for: {description}\n"
            f"Include the filename in your description (e.g. 'in empire_daemon.py').{suggestion}"
        )

    if fuzzy_suggestions:
        logger.info(f"[AUTONOMOUS_FIX] Fuzzy matches: {fuzzy_suggestions}")

    logger.info(f"[AUTONOMOUS_FIX] Files: {relevant_files} | New: {new_files} | Dirs: {new_dirs}")

    # ── Step 2: Read all existing relevant files ───────────────────────
    # Log files (.log) → Gemma (local, zero API cost, privacy-safe)
    # Code/config files → cl_bridge read_file
    file_contents = {}
    gemma_log_summary = ""

    for path in relevant_files[:3]:
        if not path.startswith(FILE_ACCESS_PREFIX):
            continue

        # Route log files to Gemma for local analysis (zero cost)
        if path.endswith(".log") or "/logs/" in path or path.startswith("/tmp/"):
            log_content = await _cl_read_file(path)
            if not log_content.startswith("[read error"):
                # Ask Gemma to summarize the log — stays 100% local
                gemma_summary = await call_gemma(
                    "You are Gemma, a local AI assistant. Analyze this log file and summarize any errors, warnings, or anomalies relevant to the task. Be concise.",
                    [{"role": "user", "content": f"TASK: {description}\n\nLOG FILE: {path}\n\n{log_content[-8000:]}"}]
                )
                gemma_log_summary += f"=== GEMMA LOG ANALYSIS: {path} ===\n{gemma_summary}\n\n"
                logger.info(f"[AUTONOMOUS_FIX] Gemma analyzed log: {path}")
            continue

        # Standard code/config files via cl_bridge
        content = await _cl_read_file(path)
        if not content.startswith("[read error"):
            file_contents[path] = content
            logger.info(f"[AUTONOMOUS_FIX] Read {path} ({len(content)} chars)")

    if not file_contents and not new_files and not gemma_log_summary:
        return f"[#fix FAILED] Could not read any relevant files for: {description}"

    # ── Step 3: Aider executes the implementation ────────────────────
    # Architecture:
    #   Claude Sonnet  → manifest reasoning (complex logic, base-context-injected, capped)
    #   DeepSeek       → Aider's worker model (90% of tasks, cheap, volume)
    #   Gemma          → local log analysis (zero cost, already done in Step 2)
    #   Aider          → execution engine (diff-based, no token truncation, no JSON encoding)
    #
    # Aider reads files directly from disk and applies surgical diffs.
    # No full-file rewrites. No JSON encoding. Works on files of any size.
    #
    file_context = "\n\n".join([
        f"=== EXISTING FILE: {path} ===\n{content[:8000]}"
        for path, content in file_contents.items()
    ])
    if gemma_log_summary:
        file_context = gemma_log_summary + file_context

    iteration = 0
    last_error = ""
    backup_paths = {}

    # Check Aider is available
    import shutil as _shutil
    aider_path = _shutil.which("aider") or str(Path(CONDA_BIN).parent / "envs" / "peakforge-trading" / "bin" / "aider")

    while iteration < 5:
        if AUTONOMOUS_STOP_FLAG:
            return "[#fix STOPPED] Loop halted by /stop command."

        iteration += 1
        logger.info(f"[AUTONOMOUS_FIX] Iteration {iteration}/5")

        # ── Step 3a: Claude Sonnet drafts manifest (reasoning only, no file content) ──
        manifest_prompt = f"""You are CL, PeakForge Empire CTO. Plan the implementation for:

REQUEST: {description}

{f"EXISTING FILE CONTEXT:{chr(10)}{file_context}" if file_context else "No existing files — creating new."}

{f"PREVIOUS ATTEMPT FAILED: {last_error}" if last_error else ""}

Return a manifest of what needs to change. Do NOT include any file content.

Respond with JSON ONLY:
{{{{
  "files": [
    {{{{
      "path": "/absolute/path/to/file",
      "is_new": false,
      "description": "one sentence: exactly what to change and where",
      "aider_message": "precise instruction for Aider: e.g. 'Add prune_stale_threads() call after line containing self._update_health_history in run_cycle method'"
    }}}}
  ],
  "dirs_to_create": [],
  "plain_english": "One paragraph summary",
  "restart_required": false
}}}}

Rules:
- path must start with {FILE_ACCESS_PREFIX}/
- aider_message must be specific enough for Aider to act on without ambiguity
- restart_required: true if brain restart needed (router.py, chat_server.py, cl_bridge.py)
- NEVER include file content
- NEVER include [PROPOSE: ...] blocks"""

        try:
            manifest_response = await call_claude(
                "You are CL, PeakForge Empire CTO. Respond with JSON only. Be precise about what to change and where.",
                [{"role": "user", "content": manifest_prompt}]
            )
            json_match = _re.search(r'\{.*\}', manifest_response, _re.DOTALL)
            if not json_match:
                last_error = f"Manifest invalid JSON: {manifest_response[:200]}"
                continue
            manifest_data = _json.loads(json_match.group())
            files_manifest = manifest_data.get("files", [])
            dirs_to_create = manifest_data.get("dirs_to_create", [])
            plain_english = manifest_data.get("plain_english", "")
            restart_required = manifest_data.get("restart_required", False)

            if not files_manifest:
                last_error = "Manifest returned no files"
                continue

            for f in files_manifest:
                if not f.get("path", "").startswith(FILE_ACCESS_PREFIX):
                    last_error = f"Invalid path: {f.get('path')}"
                    break
            else:
                last_error = ""

            if last_error:
                continue

        except (_json.JSONDecodeError, Exception) as e:
            last_error = f"Manifest error: {e}"
            continue

        # ── Step 3b: Aider executes each file change ───────────────────
        # Aider uses DeepSeek as its model — cheap, fast, surgical diffs.
        # For new files or complex logic, falls back to Claude Sonnet.
        files_to_write = []
        aider_errors = []

        for file_spec in files_manifest:
            fpath = file_spec["path"]
            is_new = file_spec.get("is_new", False)
            aider_message = file_spec.get("aider_message", file_spec.get("description", description))

            # Choose model: Claude Sonnet for complex/new, DeepSeek for modifications
            aider_model = "anthropic/claude-sonnet-4-6" if is_new else "deepseek/deepseek-chat"

            # Build Aider command
            # --yes: non-interactive, auto-apply edits
            # --no-git: we handle backups ourselves
            # --timeout: 120s per file
            import os as _os
            env = _os.environ.copy()
            env["DEEPSEEK_API_KEY"] = _os.getenv("DEEPSEEK_API_KEY", "")
            env["ANTHROPIC_API_KEY"] = _os.getenv("ANTHROPIC_API_KEY", "")

            aider_cmd = [
                aider_path,
                "--model", aider_model,
                "--yes",
                "--no-git",
                "--message", aider_message,
                fpath
            ]

            logger.info(f"[AUTONOMOUS_FIX] Aider running on {fpath} with model {aider_model}")
            logger.info(f"[AUTONOMOUS_FIX] Aider message: {aider_message[:100]}")

            try:
                import asyncio as _asyncio
                import subprocess as _subprocess
                proc = await _asyncio.create_subprocess_exec(
                    *aider_cmd,
                    stdout=_subprocess.PIPE,
                    stderr=_subprocess.PIPE,
                    env=env,
                    cwd=str(Path(fpath).parent)
                )
                try:
                    stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=180)
                except _asyncio.TimeoutError:
                    proc.kill()
                    aider_errors.append(f"{fpath}: Aider timed out after 180s")
                    continue

                stdout_str = stdout.decode("utf-8", errors="replace")
                stderr_str = stderr.decode("utf-8", errors="replace")
                returncode = proc.returncode

                logger.info(f"[AUTONOMOUS_FIX] Aider exit code: {returncode}")
                if stdout_str:
                    logger.info(f"[AUTONOMOUS_FIX] Aider stdout: {stdout_str[:300]}")
                if stderr_str:
                    logger.info(f"[AUTONOMOUS_FIX] Aider stderr: {stderr_str[:300]}")

                if returncode != 0:
                    aider_errors.append(f"{fpath}: Aider exited {returncode} — {stderr_str[:200]}")
                    continue

                # Read back the modified file for verify step
                modified_content = await _cl_read_file(fpath)
                if modified_content.startswith("[read error"):
                    aider_errors.append(f"{fpath}: Could not read back after Aider")
                    continue

                files_to_write.append({
                    "path": fpath,
                    "content": modified_content,
                    "is_new": is_new,
                    "description": file_spec.get("description", ""),
                    "aider_output": stdout_str[:500]
                })

            except Exception as e:
                aider_errors.append(f"{fpath}: Aider execution error: {e}")
                continue

        if aider_errors:
            last_error = f"Aider errors: {'; '.join(aider_errors)}"
            logger.warning(f"[AUTONOMOUS_FIX] Aider errors iteration {iteration}: {last_error}")
            continue

        if not files_to_write:
            last_error = "Aider produced no file changes"
            continue

        # ── Step 4: GEP + GRP plain-English review of manifest ─────────
        file_summary = "\n".join([
            f"  - {'CREATE' if f.get('is_new') else 'MODIFY'} {f['path']}: {f.get('description', '')}"
            for f in files_to_write
        ])

        review_prompt = f"""Dalen has requested:
"{description}"

Claude (CL) has produced this implementation plan:
{file_summary}

Summary: {plain_english}

Review this plan and respond in plain English a non-technical CEO can understand.
Be brief — 2-3 sentences. State clearly: SAFE TO APPLY or DO NOT APPLY and why.

Only veto (DO NOT APPLY) if:
- A file that should not be touched is being modified
- The plan would delete or corrupt critical data
- The implementation is completely wrong for what was asked

Do NOT veto for style or implementation preferences.
CRITICAL: No code, no commands, no [PROPOSE:] blocks. Plain English only."""

        async def _safe_gep_review():
            try:
                return await call_gep(AGENT_PERSONAS["gec"], [{"role": "user", "content": review_prompt}])
            except Exception as e:
                return f"[GEP review failed: {e}]"

        async def _safe_grp_review():
            try:
                return await call_grp(AGENT_PERSONAS["grc"], [{"role": "user", "content": review_prompt}])
            except Exception as e:
                return f"[GRP review failed: {e}]"

        gep_review, grp_review = await _asyncio.gather(_safe_gep_review(), _safe_grp_review())

        if gep_review.startswith("[GEP review failed") and grp_review.startswith("[GRP review failed"):
            last_error = f"Both council reviews failed — GEP: {gep_review} | GRP: {grp_review}"
            logger.warning(f"[AUTONOMOUS_FIX] Both reviews failed iteration {iteration}")
            continue

        veto_keywords = ["DO NOT APPLY", "DO NOT EXECUTE"]
        gep_vetoed = any(k in gep_review.upper() for k in veto_keywords)
        grp_vetoed = any(k in grp_review.upper() for k in veto_keywords)

        if gep_vetoed or grp_vetoed:
            return (
                f"[#fix — COUNCIL VETOED]\n\n"
                f"GEP: {gep_review}\n\nGRP: {grp_review}\n\n"
                f"Implementation blocked. Describe differently or ask CL for guidance."
            )

        # ── Step 5: Create directories ─────────────────────────────────
        for dir_path in dirs_to_create:
            if not dir_path.startswith(FILE_ACCESS_PREFIX):
                continue
            mkdir_cmd = f"python3 -c \"import os; os.makedirs('{dir_path}', exist_ok=True); print('Created: {dir_path}')\""
            mkdir_result = await _cl_execute(mkdir_cmd, working_dir=str(_PF_ROOT))
            if not mkdir_result["ok"]:
                logger.warning(f"[AUTONOMOUS_FIX] mkdir failed: {dir_path}: {mkdir_result.get('error')}")
            else:
                logger.info(f"[AUTONOMOUS_FIX] Created dir: {dir_path}")

        # ── Step 6: Aider already wrote files — record backups for report ──
        # Aider applies diffs directly to disk. Content read back in Step 3b.
        for file_spec in files_to_write:
            fpath = file_spec["path"]
            is_new = file_spec.get("is_new", False)
            if not is_new and fpath not in backup_paths:
                bp = await _auto_backup(fpath)
                backup_paths[fpath] = bp
                logger.info(f"[AUTONOMOUS_FIX] Backup recorded: {bp}")

                # ── Step 7: Verify all written files ──────────────────────────
        verify_results = []
        all_verified = True

        for file_spec in files_to_write:
            fpath = file_spec["path"]
            exec_working_dir = str(Path(fpath).parent)
            ext = Path(fpath).suffix.lower()

            if ext == ".py":
                verify_result = await _cl_execute(
                    f"python3 -m py_compile {fpath}",
                    working_dir=exec_working_dir
                )
                ok = verify_result.get("ok") and verify_result.get("returncode", 1) == 0
                method = "py_compile"
                detail = verify_result.get("stderr", "") if not ok else "clean"
            else:
                readback = await _cl_read_file(fpath)
                ok = not readback.startswith("[read error") and len(readback.strip()) > 0
                method = "read-back"
                detail = f"{len(readback)} chars" if ok else readback[:100]

            verify_results.append({"path": fpath, "ok": ok, "method": method, "detail": detail})
            if not ok:
                all_verified = False
                logger.warning(f"[AUTONOMOUS_FIX] Verify failed {fpath} ({method}): {detail}")
                if fpath in backup_paths and not backup_paths[fpath].startswith("[backup failed"):
                    restore_cmd = f"python3 -c \"import shutil; shutil.copy({repr(backup_paths[fpath])}, {repr(fpath)})\""
                    await _cl_execute(restore_cmd, working_dir=exec_working_dir)

        if not all_verified:
            failed = [v["path"] for v in verify_results if not v["ok"]]
            last_error = f"Verification failed for: {', '.join(failed)}"
            continue

        # ── Step 8: Success report ─────────────────────────────────────
        verify_summary = "\n".join([
            f"  {'✓' if v['ok'] else '✗'} {v['path']} ({v['method']})"
            for v in verify_results
        ])
        backup_summary = "\n".join([
            f"  {fpath}: {bp}"
            for fpath, bp in backup_paths.items()
        ]) or "  N/A (new files only)"
        restart_note = "\n\n⚠️ Brain restart required — run `restart-brain` from PM terminal." if restart_required else ""

        # Aider output summary
        aider_summary = "\n".join([
            f"  {f['path']}: {f.get('aider_output', 'applied')[:120]}"
            for f in files_to_write
        ])

        report = f"""[COUNCIL-CERTIFIED BUILD APPLIED — Aider Engine]

Request: {description}

What was done:
{plain_english}

Files changed ({len(files_to_write)}):
{file_summary}

Aider output:
{aider_summary}

Verification:
{verify_summary}

Backups:
{backup_summary}

Iterations: {iteration}
Engine: Aider + DeepSeek (manifest: Claude Sonnet)

GEP review: {gep_review}

GRP review: {grp_review}{restart_note}"""

        logger.info(f"[AUTONOMOUS_FIX] Success on iteration {iteration}")
        return report

    # ── Max iterations reached ─────────────────────────────────────────
    return f"""[#fix — ESCALATION REQUIRED]

Request: {description}
Attempts: 5 (max reached)
Last error: {last_error}

Could not complete after 5 attempts. Describe differently or escalate to GRM."""

CLAW_INBOX_PATH = _PF_ROOT / "DeadDrop" / "CLAW_INBOX.json"

PYDANTIC_COUNCIL_AGENT = _PF_ROOT / "PydanticCouncil" / "agent_cl.py"

async def handle_plan_command(prompt: str) -> str:
    """
    /plan [question] — PydanticCouncil autonomous round-trip.

    Subprocess-invokes agent_cl.py --round-trip. CL frames a role-tagged ask,
    GEMP + GRM respond in parallel (independence-before-convergence), CL
    synthesizes. Latency/integrity thresholds live in
    PydanticCouncil/DEFAULT_SURFACE_SAFETY.md.
    """
    import asyncio as _asyncio
    import subprocess as _subprocess

    if not PYDANTIC_COUNCIL_AGENT.is_file():
        return f"[/plan ERROR] agent_cl.py not found at {PYDANTIC_COUNCIL_AGENT}"

    cmd = [
        "python3",
        str(PYDANTIC_COUNCIL_AGENT),
        "--round-trip",
        "--prompt",
        prompt,
    ]
    try:
        proc = await _asyncio.create_subprocess_exec(
            *cmd,
            stdout=_subprocess.PIPE,
            stderr=_subprocess.PIPE,
        )
        # 180s cap — well above the ≤75s cold threshold, still guards runaway
        stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=180)
        if proc.returncode != 0:
            return (
                f"[/plan FAILED] exit={proc.returncode}\n"
                f"stderr: {stderr.decode('utf-8', errors='replace')[:1500]}"
            )
        return stdout.decode("utf-8", errors="replace").strip() or "[/plan returned empty output]"
    except _asyncio.TimeoutError:
        return "[/plan TIMEOUT] round-trip exceeded 180s cap — see DEFAULT_SURFACE_SAFETY.md kill-switch."
    except Exception as e:
        return f"[/plan ERROR] {type(e).__name__}: {e}"

async def handle_cascade_command(goal: str, committee: str) -> str:
    """
    /cascade [plain English goal] — Dalen's async execution entry point.

    Translates plain English into a CLAW_INBOX.json Dead Drop directive,
    writes it atomically, and lets claw_bridge + OpenClaw do the rest.
    Dalen never sees JSON. GEP/GRP never draft JSON.
    Results post back to the council tab automatically via OUTBOX.

    This is the primary interface for all async tasks:
    - Filesystem audits
    - Multi-step cascades
    - Long-running simulations or backtests
    - Anything that takes >2 minutes or where Dalen is walking away
    """
    if not goal:
        return (
            "[/cascade] Usage: /cascade [plain English goal]\n"
            "Example: /cascade run a full filesystem audit across Ecosphere and scripts\n"
            "Example: /cascade backtest Bot 15 across 2021-2026\n"
            "Example: /cascade audit all LaunchAgent plists and report status"
        )

    import time as _time
    import re as _re

    # Generate a descriptive task_id from the goal
    slug = _re.sub(r'[^a-z0-9]+', '-', goal.lower().strip())[:40].strip('-')
    task_id = f"{slug}-{int(_time.time())}"

    payload = {
        "task_id": task_id,
        "step": 1,
        "status": "PENDING",
        "objective": goal,
        "previous_results": "",
        "directive": goal,
        "max_retries": 3,
        "retry_count": 0,
        "timeout_seconds": 300,
        "source_committee": committee,
    }

    try:
        # Ensure DeadDrop directory exists
        CLAW_INBOX_PATH.parent.mkdir(parents=True, exist_ok=True)

        # Atomic write — temp file + rename to prevent claw_bridge partial reads
        import tempfile as _tempfile
        import os as _os
        tmp_path = CLAW_INBOX_PATH.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        _os.replace(tmp_path, CLAW_INBOX_PATH)

        logger.info(f"[CASCADE] Written to CLAW_INBOX: task_id={task_id}")

        return (
            f"✓ Cascade dispatched — task_id: {task_id}\n\n"
            f"Goal: {goal}\n\n"
            f"claw_bridge will pick this up within 30s. "
            f"Results will post back to this tab automatically via OUTBOX. "
            f"You can walk away — the council will have findings when you return."
        )

    except Exception as e:
        logger.error(f"[CASCADE] Failed to write CLAW_INBOX: {e}")
        return f"[/cascade ERROR] Could not write to Dead Drop: {e}"
