import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "council_history.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            session_id TEXT NOT NULL,
            committee TEXT NOT NULL DEFAULT 'council',
            speaker TEXT NOT NULL,
            content TEXT NOT NULL,
            flagged INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def save_message(session_id: str, committee: str, speaker: str, content: str, flagged: bool = False):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO messages (timestamp, session_id, committee, speaker, content, flagged) VALUES (?,?,?,?,?,?)",
        (str(time.time()), session_id, committee, speaker, content, 1 if flagged else 0)
    )
    conn.commit()
    conn.close()

def load_recent(committee: str, n: int = 20) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT speaker, content, timestamp FROM messages WHERE committee=? ORDER BY id DESC LIMIT ?",
        (committee, n)
    ).fetchall()
    conn.close()
    return [{"speaker": r[0], "content": r[1], "timestamp": r[2]} for r in reversed(rows)]

def load_all(committee: str) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT speaker, content, timestamp FROM messages WHERE committee=? ORDER BY id ASC",
        (committee,)
    ).fetchall()
    conn.close()
    return [{"speaker": r[0], "content": r[1], "timestamp": r[2]} for r in rows]

def load_today(committee: str) -> list[dict]:
    """Load all messages from the last 24 hours for the given committee.
    Uses sliding window instead of calendar day to avoid morning-after empty returns."""
    import time
    start_ts = time.time() - 86400
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT speaker, content, timestamp FROM messages "
        "WHERE committee=? AND CAST(timestamp AS REAL) >= ? "
        "ORDER BY id ASC",
        (committee, start_ts)
    ).fetchall()
    conn.close()
    return [{"speaker": r[0], "content": r[1], "timestamp": r[2]} for r in rows]
