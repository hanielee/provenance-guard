import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "audit.db"


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS submissions (
                content_id        TEXT PRIMARY KEY,
                creator_id        TEXT NOT NULL,
                timestamp         TEXT NOT NULL,
                content_type      TEXT NOT NULL DEFAULT 'text',
                content_text      TEXT,
                attribution       TEXT NOT NULL,
                confidence        REAL NOT NULL,
                llm_score         REAL NOT NULL,
                stylometric_score REAL NOT NULL,
                linguistic_score  REAL,
                ensemble_method   TEXT NOT NULL DEFAULT 'weighted',
                label             TEXT NOT NULL,
                status            TEXT NOT NULL DEFAULT 'classified'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS appeals (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id        TEXT NOT NULL,
                creator_reasoning TEXT NOT NULL,
                timestamp         TEXT NOT NULL,
                FOREIGN KEY (content_id) REFERENCES submissions (content_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS certificates (
                certificate_id      TEXT PRIMARY KEY,
                content_id          TEXT NOT NULL UNIQUE,
                creator_id          TEXT NOT NULL,
                process_description TEXT NOT NULL,
                issued_at           TEXT NOT NULL,
                FOREIGN KEY (content_id) REFERENCES submissions (content_id)
            )
        """)
        conn.commit()


def log_submission(entry: dict):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO submissions
            (content_id, creator_id, timestamp, content_type, content_text,
             attribution, confidence, llm_score, stylometric_score,
             linguistic_score, ensemble_method, label, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry["content_id"], entry["creator_id"], entry["timestamp"],
            entry.get("content_type", "text"),
            entry.get("content_text"),
            entry["attribution"], entry["confidence"],
            entry["llm_score"], entry["stylometric_score"],
            entry.get("linguistic_score"),
            entry.get("ensemble_method", "weighted"),
            entry["label"], entry["status"],
        ))
        conn.commit()


def log_appeal(content_id: str, reasoning: str, timestamp: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO appeals (content_id, creator_reasoning, timestamp) VALUES (?, ?, ?)",
            (content_id, reasoning, timestamp),
        )
        conn.commit()


def log_certificate(cert_id: str, content_id: str, creator_id: str, description: str, issued_at: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO certificates (certificate_id, content_id, creator_id, process_description, issued_at)
            VALUES (?, ?, ?, ?, ?)
        """, (cert_id, content_id, creator_id, description, issued_at))
        conn.commit()


def update_status(content_id: str, status: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE submissions SET status = ? WHERE content_id = ?",
            (status, content_id),
        )
        conn.commit()


def get_submission(content_id: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM submissions WHERE content_id = ?", (content_id,)
        ).fetchone()
        return dict(row) if row else None


def get_certificate(content_id: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM certificates WHERE content_id = ?", (content_id,)
        ).fetchone()
        return dict(row) if row else None


def get_log_entries(limit: int = 50) -> list:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT s.*,
                   a.creator_reasoning AS appeal_reasoning,
                   a.timestamp         AS appeal_timestamp,
                   c.certificate_id    AS certificate_id,
                   c.issued_at         AS certificate_issued_at
            FROM submissions s
            LEFT JOIN appeals      a ON s.content_id = a.content_id
            LEFT JOIN certificates c ON s.content_id = c.content_id
            ORDER BY s.timestamp DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_analytics() -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        totals = conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN attribution = 'likely_ai'    THEN 1 ELSE 0 END) AS ai_count,
                SUM(CASE WHEN attribution = 'uncertain'    THEN 1 ELSE 0 END) AS uncertain_count,
                SUM(CASE WHEN attribution = 'likely_human' THEN 1 ELSE 0 END) AS human_count,
                AVG(confidence) AS avg_confidence,
                SUM(CASE WHEN content_type = 'metadata'   THEN 1 ELSE 0 END) AS metadata_count,
                SUM(CASE WHEN content_type = 'text'       THEN 1 ELSE 0 END) AS text_count
            FROM submissions
        """).fetchone()

        appeal_count = conn.execute("SELECT COUNT(*) FROM appeals").fetchone()[0]
        cert_count   = conn.execute("SELECT COUNT(*) FROM certificates").fetchone()[0]

        total = totals["total"] or 0
        return {
            "total_submissions":    total,
            "verdict_distribution": {
                "likely_ai":    totals["ai_count"]       or 0,
                "uncertain":    totals["uncertain_count"] or 0,
                "likely_human": totals["human_count"]    or 0,
            },
            "appeal_count":    appeal_count,
            "appeal_rate_pct": round((appeal_count / total * 100) if total else 0, 1),
            "avg_confidence":  round(totals["avg_confidence"] or 0, 4),
            "certificate_count": cert_count,
            "content_type_breakdown": {
                "text":     totals["text_count"]     or 0,
                "metadata": totals["metadata_count"] or 0,
            },
        }
