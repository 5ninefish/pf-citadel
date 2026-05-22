import json
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
from db import load_all, load_recent
from config import (
    FOUNDING_BRIEF_PATH,
    CANONICAL_SPEC_PATH,
    ENV_INVENTORY_PATH,
    EMPIREWIKI_RAW_DIR as EOD_RAW_DIR,
    DRIFT_REGISTER_PATH,
    GBRAIN_BIN,
    LEDGER_PATH,
    WIKI_INDEX_PATH,
    HEALTH_PRIMER_PATH,
    EMPIRE_RESEARCH_PATH,
    PF_SHARED_DIR as EOD_DIR,
    SCAVENGES_INDEX_PATH,
)

GBRAIN_TIMEOUT_S = 4  # fail-fast; build_context must stay sub-second

# Per-source slice caps for composed base context.
FOUNDING_CAP = 6000
# CANONICAL_CAP retired — canonical now loaded via _load_canonical_smart() which
# always surfaces §14 (hardening tests) and §15 (laws) regardless of file growth.
# Prior cap of 8000 (then 25000) cut off §14 which starts at byte ~27,400. (2026-04-25)
ENV_CAP = 4000
# Hard cap on recent messages injected into dynamic context. Guards against
# council_history.db growth inflating every GRP/GEP call indefinitely.
RECENT_MSG_CAP = 50
EOD_CAP = 6000
DRIFT_REGISTER_CAP = 8000  # generous cap; drift register is a critical safety artifact
CANONICAL_MIDDLE_CAP = 15000  # bytes of §1–§13 to include before jumping to §14+


def _latest_eod_path() -> Path | None:
    if not EOD_RAW_DIR.exists():
        return None
    eods = sorted(EOD_RAW_DIR.glob("EOD_CONTINUITY_*.md"), reverse=True)
    return eods[0] if eods else None


def _eod_slug(path: Path) -> str:
    """Derive the gbrain slug used by bulk-flush (2026-04-23) from an EOD filename.

    Slug rule: strip `.md`, lowercase. Example:
    `EOD_CONTINUITY_2026-04-22_CL.md` -> `eod_continuity_2026-04-22_cl`.
    """
    return path.stem.lower()


def _gbrain_run(args: list[str]) -> str:
    """Run `gbrain <args>` best-effort. Returns stdout on exit 0, empty on any
    failure (missing binary, timeout, nonzero exit, schema mismatch).

    This is a defensive wrapper: L2 reads are additive context, never required.
    If gbrain is offline for any reason, build_context falls through silently.
    """
    try:
        r = subprocess.run(
            [GBRAIN_BIN, *args],
            capture_output=True,
            text=True,
            timeout=GBRAIN_TIMEOUT_S,
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return ""


def _gbrain_search_hits(query: str, limit: int = 5) -> list[str]:
    """L2 search over flushed pages. Returns whole-chunk previews.

    gbrain CLI emits multi-line chunks: each hit starts with a '[score] slug -- text'
    line; subsequent lines are chunk body. The --json flag is silently ignored by
    gbrain v0.19.0 (verified 2026-04-24 — returns same plain text regardless).
    Prior naive splitlines()[:limit] parser fragmented chunks into orphaned header
    lines — fixed 2026-04-25 T2 Option B: group lines by [score] boundary.

    P2 reranker (2026-04-28): gbrain graph leg contributes near-zero signal when
    149/163 pages are orphans. Post-retrieval re-sort blends gbrain score with
    query-term presence to improve P@5 ordering without touching gbrain internals.
    """
    import re as _re
    # Use hybrid `query` (vector + keyword) instead of `search` (FTS/keyword only).
    # FTS biases toward large documents (EODs, arch deltas dominate by term count);
    # hybrid surfaces small high-value scavenges correctly. Switched 2026-04-27.
    out = _gbrain_run(["query", query, "--limit", str(limit)])
    if not out:
        return []
    out = out.strip()
    hit_start = _re.compile(r'^\[(\d+\.\d+)\]')  # capture score for reranker
    chunks: list[tuple[float, list[str]]] = []    # (gbrain_score, lines)
    current: list[str] = []
    current_score: float = 0.0
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = hit_start.match(stripped)
        if m:
            if current:
                chunks.append((current_score, current))
            current_score = float(m.group(1))
            current = [stripped]
        elif current:
            current.append(stripped)
    if current:
        chunks.append((current_score, current))
    # Format guard: if output was non-empty but parsed zero chunks, the CLI `query`
    # verb uses a different output format than expected. Log and fall back to empty
    # rather than silently mis-serving context. Next session: verify CLI output shape.
    if out and not chunks:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "gbrain query output non-empty but parsed 0 chunks — "
            "CLI format may differ from expected '[score] slug -- text'; "
            "falling back to empty L2 hits"
        )
        return []
    # P2 reranker: blend gbrain score (70%) + query-term presence (30%).
    # Term overlap is a proxy for topical relevance when graph edges are absent.
    query_tokens = set(_re.sub(r'[^\w\s]', '', query.lower()).split())
    ranked: list[tuple[float, list[str]]] = []
    for gbrain_score, lines in chunks:
        if query_tokens:
            chunk_text = ' '.join(lines).lower()
            overlap = sum(1 for t in query_tokens if t in chunk_text) / len(query_tokens)
        else:
            overlap = 0.0
        ranked.append((gbrain_score * 0.7 + overlap * 0.3, lines))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ["\n".join(lines)[:300] for _, lines in ranked[:limit]]


def _gbrain_timeline(slug: str, max_entries: int = 6) -> list[str]:
    """Read timeline entries attached to a specific gbrain page.

    Timeline entries accumulate as council decisions get `add_timeline_entry`
    calls tagged against canonical slugs (latest EOD, arch delta, memos).
    Best-effort: returns empty list if the timeline CLI surface differs from
    expected, if the slug has no entries, or if gbrain is offline.
    """
    if not slug:
        return []
    # Try the expected surface first. If gbrain exposes a different verb, the
    # defensive wrapper returns "" and we fall through to empty.
    out = _gbrain_run(["timeline", slug, "--limit", str(max_entries), "--json"])
    if not out:
        return []
    try:
        data = json.loads(out.strip())
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    entries = []
    for entry in data[:max_entries]:
        if not isinstance(entry, dict):
            continue
        when = entry.get("at") or entry.get("ts") or entry.get("date") or ""
        body = (entry.get("body") or entry.get("text") or entry.get("summary") or "").strip()
        if body:
            entries.append(f"{when} — {body}"[:300] if when else body[:300])
    return entries


def _recent_decision_slugs(days: int = 14) -> list[str]:
    """Discover recent decision and originals page slugs by date-prefixed filename.

    Scans `EmpireWiki/raw/decisions/*.md` and `EmpireWiki/raw/originals/*.md`,
    filters to filenames starting with a date prefix in the last `days` days
    (format `YYYY-MM-DD_*.md`), derives gbrain slugs per the
    `empirewiki_compile.py::_derive_slug` convention (`<taxonomy>/<basename>`,
    lowercased).

    This closes the T2 retrieval gap surfaced 2026-04-24: timeline entries
    attached to decision/original pages were invisible to `build_context()`
    because retrieval was hardcoded to the latest EOD's slug only. Per
    canonical §15.47 (files-canonical rule), institutional content lives in
    `decisions/` and `originals/` taxonomy paths under EmpireWiki/raw/; this
    function surfaces their timeline contributions into Citadel context.
    """
    import datetime as _dt
    cutoff = (_dt.date.today() - _dt.timedelta(days=days)).isoformat()
    slugs: list[str] = []
    for taxonomy in ("decisions", "originals"):
        d = EOD_RAW_DIR / taxonomy
        if not d.exists():
            continue
        for p in sorted(d.glob("*.md"), reverse=True):
            stem = p.stem  # e.g., "2026-04-24_citadel_hardening_5_tests"
            # Filename must start with YYYY-MM-DD_ to be considered dated.
            if len(stem) < 10 or stem[4] != "-" or stem[7] != "-":
                continue
            date_prefix = stem[:10]
            if date_prefix < cutoff:
                continue
            slugs.append(f"{taxonomy}/{stem}".lower())
    return slugs


def _slice(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + "\n...[truncated]"


def _load_canonical_smart(path: Path) -> str:
    """Load canonical with smart section extraction.

    Always includes §14 (hardening tests) and §15 (laws) regardless of file
    growth. Prior fixed-cap approach cut off §14 which starts at byte ~27,400;
    this function guarantees §14+ is always in context. §1–§13 are included up
    to CANONICAL_MIDDLE_CAP bytes then elided with a note. Added 2026-04-25.
    """
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    sec14 = text.find("\n## §14 ")
    if sec14 == -1:
        # Section markers absent — return full text (file is small or restructured)
        return text
    if sec14 <= CANONICAL_MIDDLE_CAP:
        # Short enough: include everything
        return text
    # Middle sections exceed budget: include header + CANONICAL_MIDDLE_CAP bytes,
    # elide the rest of §1–§13, then include §14 onwards in full.
    elision = (
        f"\n\n...[§1–§13 elided at {CANONICAL_MIDDLE_CAP} chars to preserve §14+ in context;"
        f" full canonical at /Users/peakforge/PeakForge-Ecosphere/PEAKFORGE_ARCHITECTURE_CANONICAL.md]...\n"
    )
    return text[:CANONICAL_MIDDLE_CAP] + elision + text[sec14:]


def load_text(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""

def _load_priority_docs() -> list[tuple[str, str]]:
    """Load recent EODs and EMPIRE_RESEARCH entries as priority context."""
    docs = []
    # Most recent EOD file
    if EOD_DIR.exists():
        eods = sorted(EOD_DIR.glob("EOD_CONTINUITY_*.md"), reverse=True)
        if eods:
            try:
                docs.append(("EOD", eods[0].read_text(encoding="utf-8")[:2000]))
            except Exception:
                pass
    # EMPIRE_RESEARCH ledger
    if EMPIRE_RESEARCH_PATH.exists():
        try:
            docs.append(("RESEARCH", EMPIRE_RESEARCH_PATH.read_text(encoding="utf-8")[:2000]))
        except Exception:
            pass
    return docs


def semantic_retrieve(query: str, committee: str, top_n: int = 8) -> list[str]:
    all_msgs = load_all(committee)
    priority_docs = _load_priority_docs()

    # Build corpus: priority docs first (they get a 1.5x score boost), then history
    priority_contents = [content for _, content in priority_docs]
    history_contents = [m["content"] for m in all_msgs]
    corpus = priority_contents + history_contents

    if len(corpus) < 2:
        return []
    try:
        vec = TfidfVectorizer(stop_words="english")
        matrix = vec.fit_transform(corpus + [query])
        scores = cosine_similarity(matrix[-1], matrix[:-1]).flatten()

        # Boost priority doc scores by 1.5x
        n_priority = len(priority_contents)
        scores[:n_priority] *= 1.5

        top_indices = np.argsort(scores)[::-1][:top_n]
        results = []
        for i in top_indices:
            if i < n_priority:
                label, content = priority_docs[i]
                results.append(f"[{label}]: {content[:400]}")
            else:
                msg = all_msgs[i - n_priority]
                results.append(f"[{msg['speaker']}]: {msg['content']}")
        return results
    except Exception:
        return []

def build_context_split(query: str, committee: str) -> tuple[str, str]:
    """Return (static_context, dynamic_context) for T3 prompt caching.

    T3 fix (2026-04-30): prior build_context() mixed query-dependent gbrain
    search hits with file-based static content in one string → cache_control:
    ephemeral got a different hash on every call → ~38K tokens billed fresh
    each time instead of ~2K.

    Split discipline:
    - static_context  → file reads + slug-based L2 timeline reads.
                        Content changes only when files change on disk or when
                        gbrain timeline entries accumulate (infrequent mid-session).
                        Apply cache_control: ephemeral on this block.
    - dynamic_context → gbrain health probe, session date, query-dependent L2
                        search hits, TF-IDF semantic retrieval, recent messages.
                        Changes on every call. No cache_control.
    """
    static_parts: list[str] = []

    # ── STATIC — file-based, deterministic per session ─────────────────────────

    founding = load_text(FOUNDING_BRIEF_PATH)
    if founding:
        static_parts.append(f"=== FOUNDING BRIEF (vision) ===\n{_slice(founding, FOUNDING_CAP)}")

    canonical = _load_canonical_smart(CANONICAL_SPEC_PATH)
    if canonical:
        static_parts.append(f"=== CANONICAL SPEC (architecture) ===\n{canonical}")

    env = load_text(ENV_INVENTORY_PATH)
    if env:
        static_parts.append(f"=== ENV INVENTORY (tools/credentials/infra) ===\n{_slice(env, ENV_CAP)}")

    eod_path = _latest_eod_path()
    if eod_path:
        eod_text = load_text(eod_path)
        if eod_text:
            static_parts.append(f"=== LATEST EOD ({eod_path.name}) ===\n{_slice(eod_text, EOD_CAP)}")

    # Drift register — always from filesystem, never via search.
    drift = load_text(DRIFT_REGISTER_PATH)
    if drift:
        static_parts.append(
            f"=== DRIFT REGISTER (anti-drift log — read before any deletion or strip proposal) ===\n"
            f"{_slice(drift, DRIFT_REGISTER_CAP)}"
        )

    scavenges_index = load_text(SCAVENGES_INDEX_PATH)
    if scavenges_index:
        static_parts.append(f"=== SCAVENGES INDEX ===\n{scavenges_index}")

    if committee == "health":
        health_primer = load_text(HEALTH_PRIMER_PATH)
        if health_primer:
            static_parts.append(f"=== HEALTH COUNCIL CONTEXT ===\n{health_primer}")

    if committee not in ("tech", "trading", "secops"):
        wiki_index = load_text(WIKI_INDEX_PATH)
        if wiki_index:
            wiki_preview = wiki_index[:3000] + ("...[wiki truncated]" if len(wiki_index) > 3000 else "")
            static_parts.append(f"=== EMPIRE WIKI (compiled knowledge) ===\n{wiki_preview}")

    static_context = "\n\n".join(static_parts)

    # ── DYNAMIC — query/message-dependent, billed fresh every call ─────────────

    dynamic_parts: list[str] = []

    # §0 Gate 10 — gbrain health probe. Result is session-dependent but billed
    # as dynamic since the loud-fail literal must always reach the models.
    if not _gbrain_run(["list", "--limit", "1"]):
        dynamic_parts.append(
            "[gbrain MCP not reachable — this session is running against local files only, not the L2 substrate]"
        )

    # Session date — changes at midnight HST; must be authoritative each call.
    HST = timezone(timedelta(hours=-10))
    today_str = datetime.now(tz=HST).strftime("%Y-%m-%d (%A) HST")
    dynamic_parts.append(f"=== SESSION DATE ===\nToday is {today_str}.")

    # EOD timeline — gbrain network call; result varies as entries accumulate.
    if eod_path:
        tl_entries = _gbrain_timeline(_eod_slug(eod_path))
        if tl_entries:
            dynamic_parts.append(
                "=== L2 TIMELINE (latest EOD) ===\n" + "\n".join(f"- {e}" for e in tl_entries)
            )

    # Recent decision timelines — gbrain network calls; vary during session.
    recent_slugs = _recent_decision_slugs(days=14)
    if recent_slugs:
        decision_tl: list[str] = []
        for s in recent_slugs[:8]:
            entries = _gbrain_timeline(s, max_entries=4)
            for e in entries:
                decision_tl.append(f"[{s}] {e}")
        if decision_tl:
            dynamic_parts.append(
                "=== L2 TIMELINE (recent decisions/originals) ===\n"
                + "\n".join(f"- {e}" for e in decision_tl)
            )

    # Council ledger — written to during session via /approve; must be dynamic.
    if committee not in ("tech", "trading", "secops"):
        ledger = load_text(LEDGER_PATH)
        if ledger:
            dynamic_parts.append(f"=== COUNCIL LEDGER ===\n{ledger}")

    # L2 semantic hits — query-dependent, different hash every call.
    l2_hits = _gbrain_search_hits(query, limit=10)
    if l2_hits:
        dynamic_parts.append("=== L2 MEMORY HITS ===\n" + "\n".join(f"- {h}" for h in l2_hits))

    # TF-IDF semantic retrieval + recent messages — query and history-dependent.
    if committee not in ("tech", "trading", "secops"):
        retrieved = semantic_retrieve(query, committee, top_n=8)
        if retrieved:
            dynamic_parts.append("=== RELEVANT HISTORY ===\n" + "\n".join(retrieved))

    recent = load_recent(committee, n=min(8, RECENT_MSG_CAP))
    recent_block = "\n".join(f"[{m['speaker']}]: {m['content']}" for m in recent)
    if recent_block:
        dynamic_parts.append(f"=== RECENT MESSAGES ===\n{recent_block}")

    dynamic_context = "\n\n".join(dynamic_parts)
    return static_context, dynamic_context


def build_context(query: str, committee: str) -> str:
    """Legacy single-string context. Delegates to build_context_split().

    Non-Claude callers (GEP, GRP, DeepSeek, Gemma) still receive a flat string.
    Claude callers should use build_context_split() directly for T3 caching.
    """
    static, dynamic = build_context_split(query, committee)
    return "\n\n".join(p for p in [static, dynamic] if p)
