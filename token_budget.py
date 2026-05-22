#!/usr/bin/env python3
"""
token_budget.py — Daily LLM spend tracker and budget gate.
Shared by all automated runners (asymmetry_runner, horizon_runner, etc.).

Usage:
    from token_budget import check_budget, record_usage

    ok, spent = check_budget("breakthrough")
    if not ok:
        sys.exit(0)

    # ... run the LLM call, get response ...

    record_usage("breakthrough", model, input_tokens, output_tokens)
"""
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(override=True)

HST = ZoneInfo("Pacific/Honolulu")

LOGS_DIR   = Path(__file__).parent / "logs"
SPEND_FILE = LOGS_DIR / "daily_spend.json"

DAILY_TOKEN_BUDGET = float(os.getenv("DAILY_TOKEN_BUDGET", "0.50"))

# Pricing table: (input $/MTok, output $/MTok)
_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (0.80,  4.00),
    "claude-haiku-4-5":          (0.80,  4.00),
    "claude-sonnet-4-20250514":  (3.00, 15.00),
    "claude-sonnet-4-6":         (3.00, 15.00),
    "deepseek-chat":             (0.27,  1.10),
    "deepseek-reasoner":         (0.55,  2.19),
}


def _today_hst() -> str:
    return datetime.now(HST).strftime("%Y-%m-%d")


def _load() -> dict:
    try:
        if SPEND_FILE.exists():
            data = json.loads(SPEND_FILE.read_text(encoding="utf-8"))
            if data.get("date") == _today_hst():
                return data
    except Exception:
        pass
    return {"date": _today_hst(), "total_cost": 0.0, "runs": []}


def _save(data: dict) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    SPEND_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def check_budget(runner_name: str) -> tuple[bool, float]:
    """
    Returns (within_budget, current_spend).
    Prints [BUDGET_EXCEEDED] and returns False if over limit.
    """
    data = _load()
    spent = data.get("total_cost", 0.0)
    if spent >= DAILY_TOKEN_BUDGET:
        print(
            f"[BUDGET_EXCEEDED] {runner_name}: daily spend ${spent:.4f} >= "
            f"limit ${DAILY_TOKEN_BUDGET:.2f} — skipping LLM call",
            flush=True,
        )
        return False, spent
    return True, spent


def record_usage(
    runner_name: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """
    Records usage and returns the cost of this call.
    """
    in_rate, out_rate = _PRICING.get(model, (3.00, 15.00))
    cost = (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate

    data = _load()
    data["total_cost"] = round(data.get("total_cost", 0.0) + cost, 6)
    data["runs"].append({
        "ts":            datetime.now(HST).isoformat(),
        "runner":        runner_name,
        "model":         model,
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "cost":          round(cost, 6),
    })
    _save(data)
    print(
        f"[TOKEN_BUDGET] {runner_name}: ${cost:.4f} this call "
        f"(in={input_tokens} out={output_tokens}) | "
        f"day total: ${data['total_cost']:.4f} / ${DAILY_TOKEN_BUDGET:.2f}",
        flush=True,
    )
    return cost


def read_velocity() -> dict:
    """Returns current spend state for the Station UI endpoint."""
    data = _load()
    spent  = data.get("total_cost", 0.0)
    budget = DAILY_TOKEN_BUDGET
    pct    = spent / budget if budget > 0 else 0.0
    runs   = data.get("runs", [])
    last   = runs[-1] if runs else {}
    return {
        "date":          data.get("date", _today_hst()),
        "total_cost":    round(spent, 4),
        "budget":        budget,
        "pct_used":      round(pct, 4),
        "state":         "red" if pct >= 1.0 else "yellow" if pct >= 0.5 else "green",
        "last_runner":   last.get("runner", "—"),
        "last_model":    last.get("model", "—"),
        "last_cost":     round(last.get("cost", 0.0), 4),
        "run_count":     len(runs),
    }
