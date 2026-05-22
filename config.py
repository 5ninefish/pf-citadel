"""
config.py — Centralized path and environment configuration for Citadel.

Set PF_ECOSPHERE_ROOT to your project root before launching.
All other paths derive from it automatically.

Usage:
    export PF_ECOSPHERE_ROOT=/path/to/your/ecosphere
    export GBRAIN_BIN=/path/to/gbrain/bin/gbrain   # optional
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Root ──────────────────────────────────────────────────────────────────────
PF_ROOT = Path(os.getenv("PF_ECOSPHERE_ROOT", str(Path.home() / "PeakForge-Ecosphere")))
BRAIN_DIR = Path(__file__).parent  # StrategicBrain (this file's directory)

# ── Key file paths ─────────────────────────────────────────────────────────────
FOUNDING_BRIEF_PATH    = PF_ROOT / "PEAKFORGE_FOUNDING_BRIEF.md"
CANONICAL_SPEC_PATH    = PF_ROOT / "PEAKFORGE_ARCHITECTURE_CANONICAL.md"
ENV_INVENTORY_PATH     = PF_ROOT / "ENV_INVENTORY.md"

EMPIREWIKI_RAW_DIR     = PF_ROOT / "EmpireWiki" / "raw"
EMPIREWIKI_WIKI_DIR    = PF_ROOT / "EmpireWiki" / "wiki"
DRIFT_REGISTER_PATH    = EMPIREWIKI_RAW_DIR / "drift_register.md"
SCAVENGES_INDEX_PATH   = EMPIREWIKI_RAW_DIR / "scavenges" / "INDEX.md"
WIKI_INDEX_PATH        = EMPIREWIKI_WIKI_DIR / "index.md"
EMPIRE_TASKS_PATH      = EMPIREWIKI_RAW_DIR / "EMPIRE_TASKS.md"
EMPIRE_RESEARCH_PATH   = PF_ROOT / "EmpireWiki" / "EMPIRE_RESEARCH.md"
EMPIRE_RESEARCH_PENDING = PF_ROOT / "EmpireWiki" / "EMPIRE_RESEARCH_PENDING.md"
EMPIRE_IDEAS_PENDING   = PF_ROOT / "EmpireWiki" / "EMPIRE_IDEAS_PENDING.md"

HEALTH_PRIMER_PATH     = PF_ROOT / "Health" / "HEALTH_PRIMER.md"
EMPIRE_STATE_PATH      = PF_ROOT / "PydanticOS" / "empire_state.json"

LOGS_DIR               = BRAIN_DIR / "logs"
LEDGER_PATH            = BRAIN_DIR / "COUNCIL_LEDGER.md"

# Shared cloud sync dir (iCloud or equivalent). Set PF_SHARED_DIR to override.
PF_SHARED_DIR = Path(os.getenv(
    "PF_SHARED_DIR",
    str(Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "PeakForge-Shared")
))

# ── External tools ─────────────────────────────────────────────────────────────
GBRAIN_BIN         = os.getenv("GBRAIN_BIN", str(Path.home() / "gbrain" / "bin" / "gbrain"))
CONDA_BIN          = os.getenv("CONDA_BIN", str(Path.home() / "miniforge3" / "bin"))
OPENCLAW_SESSIONS  = Path(os.getenv("OPENCLAW_SESSIONS",
                           str(Path.home() / ".openclaw" / "agents" / "main" / "sessions")))

# Allowed file-read prefix for /file slash command
FILE_ACCESS_PREFIX = os.getenv("FILE_ACCESS_PREFIX", str(Path.home()))
