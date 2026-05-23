"""
deduplicator.py — Detect and flag duplicate emails in the database.

Definition: two emails are duplicates if they share the same from_address,
normalized subject (Re:/Fwd: stripped), and body similarity >= 90%.
Among a group, the EARLIEST is the original; all later ones are duplicates.
"""

import csv
import os
import re
from collections import defaultdict

from rapidfuzz import fuzz

import db as db_module


SIMILARITY_THRESHOLD = 90.0
REPORT_PATH = os.path.join(os.path.dirname(__file__), "output", "duplicates_report.csv")


def normalize_subject(subject: str) -> str:
    """Strip Re:/Fwd: prefixes and lowercase for grouping."""
    s = subject.strip()
    s = re.sub(r'^(re|fwd?|fw)\s*:\s*', '', s, flags=re.IGNORECASE)
    return s.strip().lower()


def _body_similarity(a: str, b: str) -> float:
    """Return token_set_ratio similarity (0–100) between two body strings."""
    if not a and not b:
        return 100.0
    if not a or not b:
        return 0.0
    # Use token_set_ratio — more robust for emails with different headers
    return fuzz.token_set_ratio(a[:5000], b[:5000])


def find_duplicates(conn) -> list:
    """
    Scan all non-duplicate emails and return a list of duplicate groups.
    Each group is a list of Row objects sorted by date ascending.
    """
    rows = db_module.get_all_emails_for_dedup(conn)

    # Step 1: Group by (from_address, normalized_subject)
    groups: dict = defaultdict(list)
    for row in rows:
        key = (row["from_address"], normalize_subject(row["subject"] or ""))
        groups[key].append(row)

    # Step 2: Within each group, do pairwise body similarity
    duplicate_groups = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        # members are already sorted ASC by date (from SQL)
        # Find clusters of similar emails
        clusters = []
        used = set()
        for i, m in enumerate(members):
            if i in used:
                continue
            cluster = [m]
            for j in range(i + 1, len(members)):
                if j in used:
                    continue
                sim = _body_similarity(m["body"] or "", members[j]["body"] or "")
                if sim >= SIMILARITY_THRESHOLD:
                    cluster.append((members[j], sim))
                    used.add(j)
            if len(cluster) > 1:
                clusters.append(cluster)
                used.add(i)

        for cluster in clusters:
            # cluster[0] is the original (Row), cluster[1:] are (Row, sim) tuples
            group = {
                "original": cluster[0],
                "duplicates": [(r, s) for r, s in cluster[1:]],
            }
            duplicate_groups.append(group)

    return duplicate_groups


def flag_duplicates(conn, duplicate_groups: list) -> dict:
    """
    Write is_duplicate / duplicate_of to the database and generate the CSV report.
    Returns statistics dict.
    """
    total_flagged = 0
    report_rows = []

    for group in duplicate_groups:
        original = group["original"]
        for dup_row, sim_score in group["duplicates"]:
            db_module.mark_duplicate(conn, dup_row["message_id"], original["message_id"])
            total_flagged += 1
            report_rows.append({
                "duplicate_message_id":  dup_row["message_id"],
                "original_message_id":   original["message_id"],
                "subject":               dup_row["subject"],
                "from_address":          dup_row["from_address"],
                "duplicate_date":        dup_row["date"],
                "original_date":         original["date"],
                "similarity_score":      round(sim_score, 2),
            })

    conn.commit()

    # Write CSV report
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "duplicate_message_id", "original_message_id", "subject",
            "from_address", "duplicate_date", "original_date", "similarity_score",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(report_rows)

    stats = {
        "total_groups":   len(duplicate_groups),
        "total_flagged":  total_flagged,
        "avg_group_size": round(
            (total_flagged + len(duplicate_groups)) / max(len(duplicate_groups), 1), 2
        ),
    }
    return stats


def get_duplicate_groups_for_notification(conn) -> list:
    """
    Return groups of (latest_duplicate_row, original_row, similarity_score)
    for notification sending. Only the LATEST duplicate per group is notified.
    """
    rows = conn.execute(
        """
        SELECT e.message_id, e.date, e.from_address, e.subject,
               e.duplicate_of, e.notification_sent
        FROM emails e
        WHERE e.is_duplicate = 1 AND e.notification_sent = 0
        ORDER BY e.date DESC
        """
    ).fetchall()

    # Find the latest per original group
    seen_originals = {}
    result = []
    for row in rows:
        orig_id = row["duplicate_of"]
        if orig_id not in seen_originals:
            seen_originals[orig_id] = row
            orig = conn.execute(
                "SELECT message_id, date, subject FROM emails WHERE message_id = ?",
                (orig_id,),
            ).fetchone()
            if orig:
                # Get similarity from report (or recalculate)
                result.append({
                    "duplicate":    row,
                    "original":     orig,
                })
    return result
