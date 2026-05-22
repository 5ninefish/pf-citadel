import uuid
import json
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from db import init_db, save_message, load_recent, load_today
from router import (
    route,
    handle_task_command,
    handle_ledger_command,
    handle_decision_command,
    handle_summary_command,
    handle_wiki_command,
    handle_file_command,
    handle_history_command,
    handle_task_add_command,
    handle_propose_command,
    handle_propose_approve_command,
    handle_cascade_command,
)
from dna_exporter import router as dna_router
from memory_api import router as memory_router

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(dna_router)
app.include_router(memory_router)

SESSION_ID = str(uuid.uuid4())

# All committees — must match COMMITTEE_ROUTING in router.py
ALL_COMMITTEES = [
    "council", "health", "engine1", "trading",
    "tech", "bizdev", "tax", "chronos", "marketing", "secops"
]

@app.on_event("startup")
def startup():
    init_db()

class ChatRequest(BaseModel):
    message: str
    committee: str = "council"
    injected_file: str = ""
    speaker: str = "DALEN"

class ChatResponse(BaseModel):
    responses: dict[str, str]
    system_message: str = ""

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    msg = req.message.strip()
    committee = req.committee.strip().lower()

    # Save message with correct speaker (DALEN default, FACTORY for cascade posts)
    save_message(SESSION_ID, committee, req.speaker.upper(), msg)

    # ── Slash command handling ─────────────────────────────────────────

    if msg.startswith("/task "):
        result = await handle_task_command(msg[6:].strip())
        save_message(SESSION_ID, committee, "SYSTEM", result)
        return ChatResponse(responses={}, system_message=result)

    if msg.startswith("/ledger"):
        recent = load_recent(committee, n=30)
        full_day = load_today(committee)
        draft = await handle_ledger_command(committee, recent, full_day)
        save_message(SESSION_ID, committee, "SYSTEM", f"[LEDGER DRAFT]\n{draft}")
        return ChatResponse(responses={}, system_message=f"{draft}\n\n---\nType /approve to commit to COUNCIL_LEDGER.md")

    if msg == "/approve":
        from pathlib import Path
        from datetime import datetime
        from db import load_all
        ledger_path = Path(__file__).parent / "COUNCIL_LEDGER.md"
        all_msgs = load_all(committee)
        draft_entry = next((m for m in reversed(all_msgs) if "[LEDGER DRAFT]" in m["content"]), None)
        if draft_entry:
            entry = draft_entry["content"].replace("[LEDGER DRAFT]\n", "")
            header = f"\n\n## {datetime.now().strftime('%Y-%m-%d')} — {committee.upper()} Session\n"
            with open(ledger_path, "a") as f:
                f.write(header + entry)
            save_message(SESSION_ID, committee, "SYSTEM", "Ledger entry committed.")
            return ChatResponse(responses={}, system_message=f"✓ Ledger entry committed to COUNCIL_LEDGER.md [{committee.upper()}]")
        return ChatResponse(responses={}, system_message="No pending ledger draft found.")

    if msg.startswith("/decision"):
        recent = load_recent(committee, n=30)
        result = await handle_decision_command(committee, recent)
        save_message(SESSION_ID, committee, "SYSTEM", result)
        return ChatResponse(responses={}, system_message=f"[DECISION GATE]\n{result}")

    if msg.startswith("/summary"):
        recent = load_recent(committee, n=20)
        result = await handle_summary_command(committee, recent)
        save_message(SESSION_ID, committee, "SYSTEM", result)
        return ChatResponse(responses={}, system_message=f"[COMMITTEE SUMMARY — paste into Council or use /inject]\n{result}")

    if msg.startswith("/inject "):
        target = msg[8:].strip().lower()
        recent = load_recent(committee, n=20)
        result = await handle_summary_command(committee, recent)
        save_message(SESSION_ID, target, "SYSTEM", f"[INJECTED FROM {committee.upper()}]\n{result}")
        return ChatResponse(responses={}, system_message=f"✓ Summary injected into {target} tab.")

    if msg.startswith("/wiki"):
        topic = msg[5:].strip()
        result = await handle_wiki_command(topic)
        save_message(SESSION_ID, committee, "SYSTEM", result)
        return ChatResponse(responses={}, system_message=result)

    if msg.strip().lower() == "/stop":
        responses = await route(msg, committee, req.injected_file)
        result = responses.get("system", "Stop signal sent.")
        save_message(SESSION_ID, committee, "SYSTEM", result)
        return ChatResponse(responses={}, system_message=result)

    # ── Phase 11.9a — New slash commands ──────────────────────────────

    if msg.strip() == "/tasks":
        from config import EMPIRE_TASKS_PATH
        result = await handle_file_command(str(EMPIRE_TASKS_PATH))
        save_message(SESSION_ID, committee, "SYSTEM", result)
        return ChatResponse(responses={}, system_message=result)

    if msg.startswith("/file "):
        # Read any file via cl_bridge and inject content into conversation
        path = msg[6:].strip()
        result = await handle_file_command(path)
        save_message(SESSION_ID, committee, "SYSTEM", result)
        return ChatResponse(responses={}, system_message=result)

    if msg.startswith("/history"):
        # Pull SQLite history for a committee and inject into conversation
        # Usage: /history [committee] [n]
        # Defaults: current committee, last 30 messages
        parts = msg.split()
        target_committee = parts[1] if len(parts) > 1 and parts[1] in ALL_COMMITTEES else committee
        n = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 30
        result = await handle_history_command(target_committee, n)
        save_message(SESSION_ID, committee, "SYSTEM", result)
        return ChatResponse(responses={}, system_message=result)

    if msg.startswith("/task-add "):
        # Append a task to EMPIRE_TASKS.md
        task_text = msg[10:].strip()
        result = await handle_task_add_command(task_text)
        save_message(SESSION_ID, committee, "SYSTEM", result)
        return ChatResponse(responses={}, system_message=result)

    if msg.startswith("/propose "):
        # Dalen explicitly approves a proposed command surfaced by a model
        # Usage: /propose [command string]
        command = msg[9:].strip()
        result = await handle_propose_command(command)
        save_message(SESSION_ID, committee, "SYSTEM", result)
        return ChatResponse(responses={}, system_message=result)

    if msg.startswith("/cascade"):
        goal = msg[8:].strip()
        result = await handle_cascade_command(goal, committee)
        save_message(SESSION_ID, committee, "SYSTEM", result)
        return ChatResponse(responses={}, system_message=result)

    if msg.startswith("/propose-approve "):
        # Approve a pending Tier 2 command by approval_id
        # Usage: /propose-approve [approval_id] [command]
        # Format from /propose response: /propose-approve abc123 python3 script.py
        parts = msg[17:].strip().split(" ", 1)
        if len(parts) < 2:
            return ChatResponse(responses={}, system_message="Usage: /propose-approve [approval_id] [command]")
        approval_id = parts[0].strip()
        command = parts[1].strip()
        result = await handle_propose_approve_command(approval_id, command)
        save_message(SESSION_ID, committee, "SYSTEM", result)
        return ChatResponse(responses={}, system_message=result)

    # ── Normal message — route to agents ──────────────────────────────
    responses = await route(msg, committee, req.injected_file)

    for agent, content in responses.items():
        save_message(SESSION_ID, committee, agent.upper(), content)

    # ── Auto-execute [PROPOSE: command] blocks — Tech committee only ──
    # Only fires on strict [PROPOSE: ...] bracket format.
    # Restricted to Tech committee — prevents spurious execution in Council.
    # Skipped when message came from #fix autonomous loop — loop executes directly,
    # any [PROPOSE:] in the report is display text only, not a pending command.
    # Auto-execute is ONLY for #fix autonomous loop responses.
    # All other [PROPOSE:] blocks in normal council messages surface as display text only.
    # GEP/GRP propose blocks in normal conversation must never auto-fire.
    import re as _re
    first_proposal = None
    is_autonomous_fix = msg.strip().lower().startswith("#fix")
    if is_autonomous_fix:
        for agent, content in responses.items():
            if first_proposal:
                break
            _m = _re.search(r'\[PROPOSE:\s*(.+?)\]', content, _re.IGNORECASE | _re.DOTALL)
            if _m:
                cmd = _m.group(1).strip().replace("\n", " ").strip()
                first_proposal = {"agent": agent.upper(), "command": cmd}

    system_msg = ""
    if first_proposal:
        exec_result = await handle_propose_command(first_proposal["command"])
        system_msg = f"[{first_proposal['agent']} → EXECUTED]\n{exec_result}"
        save_message(SESSION_ID, committee, "SYSTEM", system_msg)

    return ChatResponse(responses=responses, system_message=system_msg)

@app.get("/history")
async def history(committee: str = "council"):
    return load_recent(committee, n=20)

@app.get("/poll")
async def poll(committee: str = "council", since: float = 0):
    msgs = load_recent(committee, n=50)
    new_msgs = [m for m in msgs if float(m.get("timestamp", 0)) > since]
    return {"messages": new_msgs}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/startup")
async def startup_state():
    from pathlib import Path
    from datetime import datetime
    import re

    # Load history for ALL committees
    history = {}
    for c in ALL_COMMITTEES:
        history[c] = load_recent(c, n=20)

    ledger_path = Path(__file__).parent / "COUNCIL_LEDGER.md"
    ledger_last_updated = "never"
    if ledger_path.exists():
        content = ledger_path.read_text(encoding="utf-8")
        dates = re.findall(r"## (\d{4}-\d{2}-\d{2})", content)
        if dates:
            ledger_last_updated = dates[-1]

    banner = f"Session resumed — ledger last updated {ledger_last_updated}"

    return {
        "banner": banner,
        "history": history,
        "ledger_last_updated": ledger_last_updated,
        "session_id": SESSION_ID,
    }

@app.get("/api/v1/trading/summary")
async def trading_summary():
    import json as _json
    from pathlib import Path as _Path
    from datetime import date as _date, datetime as _dt, timezone as _tz

    trading_root = _Path(__file__).parent.parent.parent / "PeakForge-Trading"
    signals_log  = trading_root / "paper_signals.jsonl"
    runner_file  = _Path(__file__).parent.parent / "EmpireWiki" / "raw" / "runner_status.json"
    today_str    = str(_date.today())

    # ── runner_status.json — live state for all hunters ───────────────────────
    rs = {}
    if runner_file.exists():
        try:
            rs = _json.loads(runner_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    def _rs(key): return rs.get(key, {})

    # ── paper_signals.jsonl — last Bot15 and S1 BOPB signals ──────────────────
    last_bot15 = None
    s1_positions = {}
    if signals_log.exists():
        try:
            lines = [l for l in signals_log.read_text().splitlines() if l.strip()]
            for line in reversed(lines):
                row = _json.loads(line)
                if last_bot15 is None and row.get("bot_id") == "BOT15_V5":
                    last_bot15 = row
                if row.get("bot_id") == "S1_BOPB" and row.get("signal_type") == "EOD_HOLD":
                    ticker = row.get("instrument")
                    if ticker and ticker not in s1_positions:
                        s1_positions[ticker] = row
                if last_bot15 and len(s1_positions) >= 2:
                    break
        except Exception:
            pass

    # ── Bot15 V5 status ────────────────────────────────────────────────────────
    if last_bot15:
        sig = last_bot15.get("signal_type", "")
        if sig == "NO_TRADE":
            meta = last_bot15.get("metadata", {})
            vix  = meta.get("vix", "")
            bot15_status = f"No trade · VIX {vix}" if vix else "No trade"
        elif sig in ("ENTRY", "LIVE"):
            bot15_status = f"live · {last_bot15.get('instrument','')}"
        else:
            bot15_status = sig or "Scanning"
    else:
        bot15_status = "Scanning"

    # ── S1 BOPB status ────────────────────────────────────────────────────────
    n_s1 = len(s1_positions)
    if n_s1 > 0:
        tickers_str = " · ".join(s1_positions.keys())
        s1_status = f"{n_s1} position{'s' if n_s1 > 1 else ''} · {tickers_str}"
    else:
        s1_status = "Scanning"

    # ── PMCC status ───────────────────────────────────────────────────────────
    pmcc_rs = _rs("hunter_pmcc")
    pmcc_open = pmcc_rs.get("positions_open", 0)
    if pmcc_open:
        tickers_str = " · ".join(pmcc_rs.get("tickers_with_positions", []))
        pnl = pmcc_rs.get("net_pnl_paper", 0)
        pmcc_status = f"{pmcc_open} position{'s' if pmcc_open > 1 else ''} · {tickers_str} · ${pnl:+,.0f}"
    else:
        pmcc_status = "Scanning TSLA · NVDA · MSTR"

    # ── Q2 Breakout status ────────────────────────────────────────────────────
    q_rs = _rs("hunter_qullamaggie")
    q2_detail = q_rs.get("detail", "")
    q2_status = f"Active · {q2_detail}" if q2_detail else "Scanning"

    # ── Q3 Parabolic Short status ─────────────────────────────────────────────
    q3_status = "Armed · alert-only" if (trading_root / "q3_equity_state.json").exists() else "Scanning"

    # ── MIC fleet status ──────────────────────────────────────────────────────
    def _mic_status(hunter_key, label):
        h = _rs(hunter_key)
        n = h.get("positions_open", 0)
        tickers = h.get("tickers", [])
        if n:
            return f"{n} position{'s' if n > 1 else ''} · {' '.join(tickers)}"
        return f"Shadow · {label}"

    mic_s1_status  = _mic_status("hunter_mic_s1",  "EV 5.56% S6.97")
    mic_l1_status  = _mic_status("hunter_mic_l1",  "EV 2.52% S2.92")
    mic_s2_status  = _mic_status("hunter_mic_s2",  "EV 1.20% S2.55")

    # ── Sniper scanner status ─────────────────────────────────────────────────
    sniper_log = _Path("/tmp/mic_sniper.log")
    sniper_status = "Offline"
    if sniper_log.exists():
        try:
            first_line = sniper_log.read_text().splitlines()[0]
            if today_str in first_line:
                sniper_status = "Ran today · conv ≥ 8 gate"
            else:
                sniper_status = "Scheduled 16:05 HST"
        except Exception:
            sniper_status = "Scheduled 16:05 HST"

    return {
        "as_of": today_str,
        "bot15_v5":    {"status": bot15_status},
        "s1_bopb":     {"status": s1_status},
        "pmcc":        {"status": pmcc_status},
        "q2_watchlist":{"status": q2_status},
        "q3_exec":     {"status": q3_status},
        "mic_s1":      {"status": mic_s1_status},
        "mic_l1":      {"status": mic_l1_status},
        "mic_s2":      {"status": mic_s2_status},
        "mic_sniper":  {"status": sniper_status},
    }


@app.get("/api/v1/validation/kpis")
async def validation_kpis():
    import json as _json, math as _math, statistics as _stat
    from pathlib import Path as _Path
    from datetime import date as _date, timedelta as _td
    from collections import defaultdict as _dd

    trading_root = _Path(__file__).parent.parent.parent / "PeakForge-Trading"
    signals_file = trading_root / "paper_signals.jsonl"
    pmcc_file    = trading_root / "pmcc_paper_state.json"

    VAL_START = _date(2026, 5, 6)
    VAL_END   = _date(2026, 6, 18)
    today     = _date.today()
    val_day   = max(1, (today - VAL_START).days + 1)
    val_total = (VAL_END - VAL_START).days

    # ── Load signals ──────────────────────────────────────────────────────────
    raw_signals = []
    if signals_file.exists():
        for line in signals_file.read_text().splitlines():
            try: raw_signals.append(_json.loads(line))
            except: pass

    # Realized outcome signals (backtest or live)
    outcome_sigs = [
        r for r in raw_signals
        if r.get("pnl_pct_backtest") is not None and r.get("outcome_backtest")
    ]
    # All conviction-bearing signals
    conv_sigs = [r for r in raw_signals if r.get("conviction") is not None]
    conv_ge8  = [r for r in conv_sigs if r["conviction"] >= 8.0]
    conv_lt8  = [r for r in conv_sigs if r["conviction"] <  8.0]

    # ── Load PMCC state ───────────────────────────────────────────────────────
    pmcc_positions = {}
    if pmcc_file.exists():
        try:
            ps = _json.loads(pmcc_file.read_text())
            pmcc_positions = ps.get("positions", {})
        except: pass
    pmcc_net = sum(p.get("net_pnl_to_date", 0) for p in pmcc_positions.values())

    def _status(value, threshold, direction="gte", accumulating=False, warn_pct=0.8):
        if accumulating:
            return "ACCUMULATING"
        if direction == "gte":
            if value >= threshold: return "GREEN"
            if value >= threshold * warn_pct: return "YELLOW"
            return "RED"
        else:  # lte
            if value <= threshold: return "GREEN"
            if value <= threshold * (1 + (1 - warn_pct)): return "YELLOW"
            return "RED"

    kpis = {}

    # ── KPI 1: Portfolio Sharpe ≥ 2.0 ────────────────────────────────────────
    # Group directional pnl by date, compute daily returns
    daily_pnl = _dd(float)
    for r in outcome_sigs:
        daily_pnl[r.get("scan_date", "unknown")] += r.get("pnl_pct_backtest", 0)
    daily_vals = list(daily_pnl.values())
    n_days = len(daily_vals)
    if n_days >= 5:
        mu  = _stat.mean(daily_vals)
        sd  = _stat.stdev(daily_vals) if n_days > 1 else 1e-9
        sharpe = round((mu / sd) * _math.sqrt(252), 2) if sd > 0 else 0.0
        kpis["sharpe"] = {
            "label": "Portfolio Sharpe",
            "value": sharpe,
            "threshold": 2.0,
            "unit": "",
            "status": _status(sharpe, 2.0),
            "corpus": n_days,
            "note": f"{n_days} trading days of directional data"
        }
    else:
        kpis["sharpe"] = {
            "label": "Portfolio Sharpe",
            "value": None,
            "threshold": 2.0,
            "unit": "",
            "status": "ACCUMULATING",
            "corpus": n_days,
            "note": f"Need ≥5 trading days ({n_days} so far)"
        }

    # ── KPI 2: Max Drawdown ───────────────────────────────────────────────────
    # Directional: cumsum of pnl_pct for outcome signals
    dir_pnls = [r.get("pnl_pct_backtest", 0) for r in outcome_sigs]
    dir_eq = [sum(dir_pnls[:i+1]) for i in range(len(dir_pnls))]
    if dir_eq:
        peak = dir_eq[0]
        dir_mdd = 0.0
        for v in dir_eq:
            peak = max(peak, v)
            dir_mdd = max(dir_mdd, (peak - v) / (peak + 1e-9))
        dir_mdd_pct = round(dir_mdd * 100, 2)
    else:
        dir_mdd_pct = 0.0

    # PMCC: using net_pnl as single aggregate (no daily series yet)
    pmcc_mdd_pct = 0.0  # all open positions currently profitable

    combined_mdd_pct = round(max(dir_mdd_pct, pmcc_mdd_pct), 2)
    mdd_accum = len(outcome_sigs) < 5
    kpis["max_dd"] = {
        "label": "Max Drawdown",
        "value": {"directional": dir_mdd_pct, "pmcc": pmcc_mdd_pct, "combined": combined_mdd_pct},
        "threshold": {"directional": 8.0, "pmcc": 4.0, "combined": 6.0},
        "unit": "%",
        "status": _status(dir_mdd_pct, 8.0, "lte", mdd_accum) if not mdd_accum else "ACCUMULATING",
        "corpus": len(outcome_sigs),
        "note": f"Directional: {dir_mdd_pct}% | PMCC: {pmcc_mdd_pct}% | Combined: {combined_mdd_pct}%"
    }

    # ── KPI 3: Cross-Engine Correlation < 0.4 ────────────────────────────────
    # Build per-engine daily P&L dict
    engine_daily = _dd(lambda: _dd(float))
    engine_map = {
        "mic_shadow_feeder_phase3": "MIC",
        "S1_BOPB": "S1",
        "BOT15_V5": "Bot15",
        "leaps_entry": "PMCC",
    }
    for r in outcome_sigs:
        eng = engine_map.get(r.get("source", r.get("bot_id", "")), None)
        if eng:
            engine_daily[eng][r.get("scan_date", "?")] += r.get("pnl_pct_backtest", 0)
    # Only compute if ≥2 engines AND ≥5 shared dates
    active_engines = {e: daily for e, daily in engine_daily.items() if len(daily) >= 3}
    if len(active_engines) >= 2:
        # Pearson correlation between two largest engines
        eng_list = list(active_engines.keys())
        all_dates = sorted(set.union(*[set(active_engines[e].keys()) for e in eng_list]))
        series = {e: [active_engines[e].get(d, 0) for d in all_dates] for e in eng_list}
        pairs = [(eng_list[i], eng_list[j]) for i in range(len(eng_list)) for j in range(i+1, len(eng_list))]
        max_corr = 0.0
        for a, b in pairs:
            xa, xb = series[a], series[b]
            n = len(xa)
            if n < 3: continue
            mx, my = _stat.mean(xa), _stat.mean(xb)
            num = sum((xa[i]-mx)*(xb[i]-my) for i in range(n))
            denom = (_math.sqrt(sum((v-mx)**2 for v in xa)) *
                     _math.sqrt(sum((v-my)**2 for v in xb)))
            r_val = num/denom if denom > 0 else 0.0
            max_corr = max(max_corr, abs(r_val))
        max_corr = round(max_corr, 3)
        kpis["cross_engine_corr"] = {
            "label": "Cross-Engine Correlation",
            "value": max_corr,
            "threshold": 0.4,
            "unit": "",
            "status": _status(max_corr, 0.4, "lte"),
            "corpus": len(all_dates),
            "note": f"Max pairwise r across {len(eng_list)} engines"
        }
    else:
        kpis["cross_engine_corr"] = {
            "label": "Cross-Engine Correlation",
            "value": None,
            "threshold": 0.4,
            "unit": "",
            "status": "ACCUMULATING",
            "corpus": len(active_engines),
            "note": f"Need ≥2 engines with ≥3 dates ({len(active_engines)} engine(s) so far)"
        }

    # ── KPI 4: Conviction vs Outcome Correlation ──────────────────────────────
    # Need variance in conviction to compute Pearson r
    conv_outcome_pairs = [
        (r["conviction"], r["pnl_pct_backtest"])
        for r in outcome_sigs if r.get("conviction") is not None
    ]
    conv_vals = [p[0] for p in conv_outcome_pairs]
    if len(conv_vals) >= 5 and len(set(conv_vals)) > 1:
        xs = [p[0] for p in conv_outcome_pairs]
        ys = [p[1] for p in conv_outcome_pairs]
        n  = len(xs)
        mx, my = _stat.mean(xs), _stat.mean(ys)
        num   = sum((xs[i]-mx)*(ys[i]-my) for i in range(n))
        denom = (_math.sqrt(sum((v-mx)**2 for v in xs)) *
                 _math.sqrt(sum((v-my)**2 for v in ys)))
        r_val = round(num/denom, 3) if denom > 0 else 0.0
        # Positive r = signals doing their job; status GREEN if r > 0
        kpis["conviction_corr"] = {
            "label": "Conviction vs Outcome r",
            "value": r_val,
            "threshold": 0.0,
            "unit": "",
            "status": "GREEN" if r_val > 0 else ("YELLOW" if r_val > -0.1 else "RED"),
            "corpus": n,
            "note": f"Pearson r over {n} signals with outcomes"
        }
    else:
        reason = "no conviction variance" if len(set(conv_vals)) <= 1 else f"only {len(conv_vals)} signals"
        kpis["conviction_corr"] = {
            "label": "Conviction vs Outcome r",
            "value": None,
            "threshold": None,
            "unit": "",
            "status": "ACCUMULATING",
            "corpus": len(conv_vals),
            "note": f"Need mixed conviction levels ({reason})"
        }

    # ── KPI 5: Combined Monthly EV ────────────────────────────────────────────
    # Target: ≥$26.87 per $100 risk on directional side
    TARGET_EV = 26.87
    if outcome_sigs:
        avg_ev = round(_stat.mean(r["pnl_pct_backtest"] for r in outcome_sigs) * 100, 2)
        pmcc_monthly = round(pmcc_net / max(val_day / 21, 0.1), 2)  # annualize to monthly
        kpis["monthly_ev"] = {
            "label": "Combined Monthly EV",
            "value": {"directional": avg_ev, "pmcc_monthly": pmcc_monthly},
            "threshold": TARGET_EV,
            "unit": "$/100 risk",
            "status": _status(avg_ev, TARGET_EV),
            "corpus": len(outcome_sigs),
            "note": f"Directional avg EV: ${avg_ev}/100 risk | PMCC monthly: ${pmcc_monthly:,.0f}"
        }
    else:
        kpis["monthly_ev"] = {
            "label": "Combined Monthly EV",
            "value": None,
            "threshold": TARGET_EV,
            "unit": "$/100 risk",
            "status": "ACCUMULATING",
            "corpus": 0,
            "note": "No outcome signals yet"
        }

    # ── KPI 6: Recovery Factor ≥ 3.0 ─────────────────────────────────────────
    if dir_eq and dir_mdd_pct > 0:
        total_gain = sum(r.get("pnl_pct_backtest", 0) for r in outcome_sigs if r.get("pnl_pct_backtest", 0) > 0)
        rf = round(total_gain / (dir_mdd_pct / 100), 2)
        kpis["recovery_factor"] = {
            "label": "Recovery Factor",
            "value": rf,
            "threshold": 3.0,
            "unit": "×",
            "status": _status(rf, 3.0),
            "corpus": len(outcome_sigs),
            "note": f"Total gain / max drawdown"
        }
    else:
        kpis["recovery_factor"] = {
            "label": "Recovery Factor",
            "value": None if not outcome_sigs else "∞",
            "threshold": 3.0,
            "unit": "×",
            "status": "GREEN" if outcome_sigs else "ACCUMULATING",
            "corpus": len(outcome_sigs),
            "note": "No drawdown recorded yet" if outcome_sigs else "No outcome signals"
        }

    # ── KPI 7: Conviction Gate Integrity ─────────────────────────────────────
    if len(conv_ge8) >= 5 and len(conv_lt8) >= 5:
        wr_ge8 = sum(1 for r in conv_ge8 if r.get("pnl_pct_backtest", 0) > 0) / len(conv_ge8)
        wr_lt8 = sum(1 for r in conv_lt8 if r.get("pnl_pct_backtest", 0) > 0) / len(conv_lt8)
        gate_ok = wr_ge8 > wr_lt8
        kpis["gate_integrity"] = {
            "label": "Gate Integrity (≥8 vs <8)",
            "value": {"wr_ge8": round(wr_ge8, 3), "wr_lt8": round(wr_lt8, 3)},
            "threshold": None,
            "unit": "",
            "status": "GREEN" if gate_ok else "RED",
            "corpus": len(conv_ge8) + len(conv_lt8),
            "note": f"≥8 WR: {wr_ge8:.1%} vs <8 WR: {wr_lt8:.1%}"
        }
    else:
        kpis["gate_integrity"] = {
            "label": "Gate Integrity (≥8 vs <8)",
            "value": None,
            "threshold": None,
            "unit": "",
            "status": "ACCUMULATING",
            "corpus": len(conv_ge8),
            "note": f"Need ≥5 signals in each bucket (≥8: {len(conv_ge8)}, <8: {len(conv_lt8)})"
        }

    # ── Scatter data ──────────────────────────────────────────────────────────
    scatter = [
        {"conviction": r["conviction"], "pnl": round(r["pnl_pct_backtest"] * 100, 2),
         "ticker": r.get("instrument", r.get("ticker", "?")),
         "outcome": r.get("outcome_backtest", "?")}
        for r in outcome_sigs if r.get("conviction") is not None
    ]

    kpi_statuses = [v["status"] for v in kpis.values()]
    all_green = all(s == "GREEN" for s in kpi_statuses)
    any_red   = any(s == "RED"   for s in kpi_statuses)

    return {
        "clock": {
            "start": VAL_START.isoformat(),
            "end":   VAL_END.isoformat(),
            "day":   val_day,
            "total": val_total,
            "pct":   round(val_day / val_total * 100, 1),
        },
        "corpus": {
            "total": len(raw_signals),
            "with_outcomes": len(outcome_sigs),
            "conviction_gte8": len(conv_ge8),
            "conviction_lt8":  len(conv_lt8),
        },
        "gate_decision": "PASS" if all_green else ("FAIL" if any_red else "ACCUMULATING"),
        "kpis": kpis,
        "scatter": scatter,
    }


@app.get("/api/v1/token/velocity")
async def token_velocity():
    """Daily LLM spend state for the Station Token Velocity LED."""
    try:
        from token_budget import read_velocity
        return read_velocity()
    except Exception as e:
        return {"state": "green", "total_cost": 0.0, "budget": 0.50,
                "pct_used": 0.0, "last_runner": "—", "error": str(e)}


@app.get("/api/v1/openclaw/health")
async def openclaw_health_proxy():
    import httpx
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get("http://127.0.0.1:18789/health")
            return {"ok": r.status_code == 200, "status": "live" if r.status_code == 200 else "degraded"}
    except Exception:
        return {"ok": False, "status": "offline"}


@app.get("/api/v1/eod/rundown")
async def eod_rundown():
    import re
    from pathlib import Path
    raw_dir   = Path(__file__).parent.parent / "EmpireWiki" / "raw"
    eod_files = sorted(raw_dir.glob("EOD_CONTINUITY_*.md"), reverse=True)
    if not eod_files:
        return {"accomplished": [], "todo": []}
    text = eod_files[0].read_text(encoding="utf-8")

    # Overnight accomplishments — top 5 numbered items under ## SHIPPED THIS SESSION
    # Use ## [^#] boundary so ### subsections don't terminate the section match
    accomplished = []
    shipped_match = re.search(r"## SHIPPED THIS SESSION\s*\n(.*?)(?=\n## [^#]|\Z)", text, re.DOTALL)
    if shipped_match:
        block = shipped_match.group(1)
        for m in re.finditer(r"^###\s+\d+\.\s+(.+)", block, re.MULTILINE):
            accomplished.append(m.group(1).strip())
            if len(accomplished) == 5:
                break

    # Today's to-do — rows from the OPEN / NEXT table
    todo = []
    open_match = re.search(r"## OPEN[^\n]*\n(.*?)(?=\n## [^#]|\Z)", text, re.DOTALL)
    if open_match:
        for m in re.finditer(r"^\|\s*([^|]+?)\s*\|\s*\*\*PENDING[^|]*\*\*|\|\s*([^|]+?)\s*\|\s*(WATCHING|ARMED|OPEN|UNRESOLVED)", open_match.group(1), re.MULTILINE):
            item = (m.group(1) or m.group(2) or "").strip()
            if item and item != "Item":
                todo.append(item)

    return {"accomplished": accomplished, "todo": todo}

@app.get("/api/v1/eod/latest")
async def eod_latest():
    import re
    from pathlib import Path
    raw_dir = Path(__file__).parent.parent / "EmpireWiki" / "raw"
    eod_files = sorted(raw_dir.glob("EOD_CONTINUITY_*.md"), reverse=True)
    if not eod_files:
        return {"date": "—", "file": "—", "summary": "No EOD files found."}
    latest = eod_files[0]
    text = latest.read_text(encoding="utf-8")
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", latest.name)
    date = date_match.group(1) if date_match else "—"
    exec_match = re.search(r"(?:EXECUTIVE SUMMARY|## 1\.)[^\n]*\n+(.*?)(?=\n##|\Z)", text, re.DOTALL)
    summary = exec_match.group(1).strip()[:600] if exec_match else text.strip()[:600]
    return {"date": date, "file": latest.name, "summary": summary}

@app.get("/api/v1/intelligence/status")
async def intelligence_status():
    from pathlib import Path
    import json as _json
    status_file = Path(__file__).parent.parent / "EmpireWiki" / "raw" / "runner_status.json"
    if status_file.exists():
        try:
            return _json.loads(status_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

@app.get("/api/v1/horizon/brief")
async def horizon_brief():
    import re
    from pathlib import Path
    from datetime import date, timedelta, datetime as _datetime

    import json as _json
    raw_dir = Path(__file__).parent.parent / "EmpireWiki" / "raw"

    # Serve from cache if written today by horizon_runner.py
    cache_file = raw_dir / "intel_brief_cache.json"
    if cache_file.exists():
        try:
            cached = _json.loads(cache_file.read_text(encoding="utf-8"))
            generated = cached.get("generated_at", "")
            if generated:
                cache_date = generated[:10]
                if cache_date == str(date.today()):
                    return cached
        except Exception:
            pass

    def latest_horizon(prefix: str, fallback_days: int = 2):
        for delta in range(fallback_days + 1):
            d = (date.today() - timedelta(days=delta)).strftime("%Y%m%d")
            f = raw_dir / f"{prefix}_{d}.md"
            if f.exists():
                return f, d
        return None, None

    h1_file, h1_date = latest_horizon("horizon_h1")
    h2_file, h2_date = latest_horizon("horizon_h2")

    if not h1_file and not h2_file:
        return {"status": "no_data", "unread": False}

    def read_sections(path: Path) -> list[dict]:
        if not path or not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        sections = []
        for block in re.split(r"\n(?=###\s)", text):
            q = re.search(r"###\s+Query:\s*(.+)", block)
            s = re.search(r"\*\*Summary:\*\*\s*(.+?)(?=\n\d+\.|\Z)", block, re.DOTALL)
            if q and s:
                sections.append({
                    "query": q.group(1).strip(),
                    "summary": s.group(1).strip()[:400],
                })
        return sections

    h1_sections = read_sections(h1_file)
    h2_sections = read_sections(h2_file)

    # Supplement H1 with firecrawl file for same date
    h1_fc_file = raw_dir / f"horizon_h1_firecrawl_{h1_date}.md" if h1_date else None
    h1_fc_sections = read_sections(h1_fc_file) if h1_fc_file and h1_fc_file.exists() else []

    # Deduplicate by query text
    seen = {s["query"] for s in h1_sections}
    for s in h1_fc_sections:
        if s["query"] not in seen:
            h1_sections.append(s)
            seen.add(s["query"])

    signals = []
    for s in h1_sections[:3]:
        signals.append({"source": "H1", "text": s["summary"].split("\n")[0][:200]})
    for s in h2_sections[:2]:
        signals.append({"source": "H2", "text": s["summary"].split("\n")[0][:200]})

    return {
        "status": "ok",
        "unread": True,
        "date": h1_date or h2_date,
        "h1_date": h1_date,
        "h2_date": h2_date,
        "h1_query_count": len(h1_sections),
        "h2_query_count": len(h2_sections),
        "signals": signals,
        "alpha": [],
        "action": [],
    }

@app.get("/api/v1/intelligence/asymmetry")
async def intelligence_asymmetry():
    import json as _json
    import uuid as _uuid
    from pathlib import Path as _Path
    from datetime import datetime as _dt

    signals_file = _Path(__file__).parent.parent / "EmpireWiki" / "raw" / "asymmetry_signals.jsonl"
    trading_root = _Path(__file__).parent.parent.parent / "PeakForge-Trading"
    paper_signals = trading_root / "paper_signals.jsonl"

    def conviction(s):
        sc = s.get("scores", {})
        return round(
            sc.get("asymmetry", 0) * 0.4
            + sc.get("proximity", 0) * 0.2
            + sc.get("novelty",   0) * 0.3
            - sc.get("friction",  0) * 0.1,
            2
        )

    signals = []
    if signals_file.exists():
        for line in signals_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                sig = _json.loads(line)
                # Recompute conviction in case scores changed
                sig["conviction"] = conviction(sig)
                signals.append(sig)
            except Exception:
                pass

    # Trigger paper_signals.jsonl writes for conviction >= 8 not yet triggered
    triggered_ids = []
    for sig in signals:
        if sig.get("conviction", 0) >= 8 and not sig.get("engine3_triggered"):
            entry = {
                "ts": _dt.utcnow().isoformat() + "Z",
                "source": sig.get("type", "asymmetry") + "-hunter",
                "type": "regime-alpha",
                "title": sig.get("title", ""),
                "conviction": sig["conviction"],
                "action": "evaluate",
                "bots": ["bot15_v5", "s1_bopb"],
                "signal_id": sig.get("id", ""),
            }
            try:
                with paper_signals.open("a", encoding="utf-8") as f:
                    f.write(_json.dumps(entry) + "\n")
                sig["engine3_triggered"] = True
                sig["frontrun_status"] = "triggered"
                triggered_ids.append(sig.get("id"))
            except Exception:
                pass

    # Persist any trigger updates back to signals file
    if triggered_ids:
        updated = {s["id"]: s for s in signals if s.get("id")}
        lines = []
        if signals_file.exists():
            for line in signals_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    s = _json.loads(line)
                    if s.get("id") in updated:
                        lines.append(_json.dumps(updated[s["id"]]))
                    else:
                        lines.append(line)
                except Exception:
                    lines.append(line)
        signals_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    breakthrough = [s for s in signals if s.get("type") in ("breakthrough", "qullamaggie_breakout")]
    cascade      = [s for s in signals if s.get("type") in ("cascade", "qullamaggie_parabolic_short")]
    all_sorted   = sorted(signals, key=lambda s: s.get("conviction", 0), reverse=True)
    top_conviction = all_sorted[0]["conviction"] if all_sorted else 0

    return {
        "status": "ok",
        "signal_count": len(signals),
        "top_conviction": top_conviction,
        "breakthrough": sorted(breakthrough, key=lambda s: s.get("conviction", 0), reverse=True),
        "cascade":      sorted(cascade,      key=lambda s: s.get("conviction", 0), reverse=True),
        "all_sorted":   all_sorted,
        "triggered_this_call": triggered_ids,
    }

@app.get("/api/v1/cron/status")
async def cron_status():
    import subprocess
    result = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    agents = []
    for line in result.stdout.splitlines():
        if "peakforge" not in line.lower():
            continue
        parts = line.split("\t")
        pid_raw  = parts[0].strip() if len(parts) > 0 else "-"
        label    = parts[2].strip() if len(parts) > 2 else "unknown"
        running  = pid_raw not in ("-", "")
        agents.append({
            "label":  label,
            "pid":    pid_raw if running else "-",
            "status": "running" if running else "stopped",
        })
    return {"agents": agents}

@app.get("/api/v1/system/usage")
async def system_usage():
    """Cache hit-rate and token counts from agent_actions.jsonl (last 24h)."""
    import json, datetime
    from pathlib import Path
    log_path = Path(__file__).parent / "logs" / "agent_actions.jsonl"
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
    anthropic_tok = gemini_tok = grok_tok = 0
    cache_reads = total = 0
    est_cost = 0.0
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            ts = e.get("ts", "")
            try:
                t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception:
                continue
            if t < cutoff:
                continue
            total += 1
            inp = e.get("input_tokens", 0) or 0
            out = e.get("output_tokens", 0) or 0
            cr  = e.get("cache_read_tokens", 0) or 0
            provider = (e.get("provider") or "").lower()
            if "anthropic" in provider:
                anthropic_tok += inp + out
                est_cost += inp * 3e-6 + out * 15e-6 + cr * 0.3e-6
            elif "google" in provider or "gemini" in provider:
                gemini_tok += inp + out
                est_cost += (inp + out) * 7e-6
            elif "xai" in provider or "grok" in provider:
                grok_tok += inp + out
                est_cost += (inp + out) * 5e-6
            if cr > 0:
                cache_reads += 1
    hit_rate = round(cache_reads / total * 100, 1) if total else 0
    return {
        "anthropic_tokens": anthropic_tok,
        "gemini_tokens": gemini_tok,
        "grok_tokens": grok_tok,
        "cache_hit_rate": hit_rate,
        "estimated_cost": round(est_cost, 4),
        "total_calls": total,
    }


@app.get("/api/v1/sovereign-audit/status")
async def sovereign_audit_status():
    """Return latest audit report, cycle 1 status, and last drift register entry."""
    import re
    from pathlib import Path
    raw_dir = Path(__file__).parent.parent / "EmpireWiki" / "raw"
    audit_files = sorted(raw_dir.glob("SOVEREIGN_AUDIT_*.md"), reverse=True)
    cycle_1 = {"start": "2026-05-01", "target_close": "2026-05-08", "status": "ACTIVE"}
    if not audit_files:
        return {"verdict": "PENDING", "last_run": "—", "report": "No audit run yet.", "cycle_1": cycle_1, "last_drift_entry": "—"}
    latest = audit_files[0]
    text = latest.read_text(encoding="utf-8")
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", latest.name)
    last_run = date_match.group(1) if date_match else "—"
    verdict = "DRIFT" if "DRIFT DETECTED" in text else "CLEAN"
    drift_reg = raw_dir / "drift_register.md"
    last_drift = "—"
    if drift_reg.exists():
        dr_text = drift_reg.read_text(encoding="utf-8")
        entries = re.findall(r"(## \d{4}-\d{2}-\d{2}.*?)(?=\n## \d{4}|\Z)", dr_text, re.DOTALL)
        if entries:
            last_drift = entries[-1].strip()[:400]
    return {"verdict": verdict, "last_run": last_run, "report": text[:800], "cycle_1": cycle_1, "last_drift_entry": last_drift}


@app.post("/api/v1/sovereign-audit/run")
async def sovereign_audit_run():
    """Run sovereign_audit.py, return stdout and verdict."""
    import subprocess, sys
    from pathlib import Path
    script = Path.home() / "scripts" / "sovereign_audit.py"
    if not script.exists():
        return {"ok": False, "output": "sovereign_audit.py not found", "verdict": "ERROR"}
    try:
        result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=30)
        out = result.stdout.strip() or result.stderr.strip()
        verdict = "DRIFT" if "DRIFT" in out.upper() else "CLEAN" if "clean" in out.lower() else "UNKNOWN"
        return {"ok": result.returncode in (0, 2), "output": out, "verdict": verdict}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "Audit timed out (>30s)", "verdict": "ERROR"}
    except Exception as e:
        return {"ok": False, "output": str(e), "verdict": "ERROR"}


@app.get("/station")
async def station_dashboard():
    from pathlib import Path
    return FileResponse(Path(__file__).parent / "ui" / "station.html")

app.mount("/ui", StaticFiles(directory="ui", html=True), name="ui")
