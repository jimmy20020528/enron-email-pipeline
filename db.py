"""
db.py — SQLite database layer for the Enron email pipeline.
"""

import sqlite3
import os


DB_PATH = os.path.join(os.path.dirname(__file__), "enron.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str = DB_PATH):
    """Create tables from schema.sql if they don't exist."""
    with open(SCHEMA_PATH, "r") as f:
        schema = f.read()
    conn = get_connection(db_path)
    conn.executescript(schema)
    conn.commit()
    conn.close()


def insert_email(conn: sqlite3.Connection, record: dict) -> int | None:
    """
    Insert an email record. Returns the new row id, or None if message_id
    already exists (duplicate insert — silently skipped).
    """
    try:
        cur = conn.execute(
            """
            INSERT INTO emails (
                message_id, date, from_address, subject, body, source_file,
                x_from, x_to, x_cc, x_bcc, x_folder, x_origin,
                content_type, has_attachment,
                forwarded_content, quoted_content, headings
            ) VALUES (
                :message_id, :date, :from_address, :subject, :body, :source_file,
                :x_from, :x_to, :x_cc, :x_bcc, :x_folder, :x_origin,
                :content_type, :has_attachment,
                :forwarded_content, :quoted_content, :headings
            )
            """,
            record,
        )
        email_id = cur.lastrowid

        # Insert recipients
        for rtype in ("to_addresses", "cc_addresses", "bcc_addresses"):
            label = rtype.replace("_addresses", "")
            for addr in record.get(rtype) or []:
                conn.execute(
                    "INSERT INTO email_recipients (email_id, address, recipient_type) VALUES (?, ?, ?)",
                    (email_id, addr, label),
                )
        return email_id

    except sqlite3.IntegrityError:
        # Duplicate message_id — skip
        return None


def log_failure(conn: sqlite3.Connection, source_file: str, reason: str):
    conn.execute(
        "INSERT INTO parse_failures (source_file, reason) VALUES (?, ?)",
        (source_file, reason),
    )


def mark_duplicate(conn: sqlite3.Connection, message_id: str, original_message_id: str):
    conn.execute(
        "UPDATE emails SET is_duplicate=1, duplicate_of=? WHERE message_id=?",
        (original_message_id, message_id),
    )


def mark_notification_sent(conn: sqlite3.Connection, message_id: str, ts: str):
    conn.execute(
        "UPDATE emails SET notification_sent=1, notification_date=? WHERE message_id=?",
        (ts, message_id),
    )


def get_all_emails_for_dedup(conn: sqlite3.Connection):
    """Return lightweight rows needed for duplicate detection."""
    return conn.execute(
        """
        SELECT id, message_id, date, from_address, subject, body
        FROM emails
        WHERE is_duplicate = 0
        ORDER BY date ASC
        """
    ).fetchall()


def get_field_completeness(conn: sqlite3.Connection) -> dict:
    """Return percentage of non-null/non-empty values for all fields (mandatory + optional)."""
    total = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    if total == 0:
        return {}

    mandatory_fields = [
        "message_id", "date", "from_address", "subject", "body", "source_file",
    ]
    optional_fields = [
        "x_from", "x_to", "x_cc", "x_bcc", "x_folder", "x_origin",
        "content_type", "has_attachment", "forwarded_content",
        "quoted_content", "headings",
    ]

    result = {"_mandatory": {}, "_optional": {}}

    for f in mandatory_fields:
        count = conn.execute(
            f"SELECT COUNT(*) FROM emails WHERE {f} IS NOT NULL AND {f} != ''"
        ).fetchone()[0]
        result["_mandatory"][f] = round(count / total * 100, 1)

    # to_addresses lives in the recipients table
    to_count = conn.execute(
        "SELECT COUNT(DISTINCT email_id) FROM email_recipients WHERE recipient_type='to'"
    ).fetchone()[0]
    result["_mandatory"]["to_addresses"] = round(to_count / total * 100, 1)

    for f in optional_fields:
        count = conn.execute(
            f"SELECT COUNT(*) FROM emails WHERE {f} IS NOT NULL AND {f} != ''"
        ).fetchone()[0]
        result["_optional"][f] = round(count / total * 100, 1)

    return result
