"""
notifier.py — Generate .eml draft files and optionally send via Gmail MCP.
"""

import csv
import os
from datetime import datetime, timezone
from email.mime.text import MIMEText


SEND_LOG_PATH  = os.path.join(os.path.dirname(__file__), "output", "send_log.csv")
REPLIES_DIR    = os.path.join(os.path.dirname(__file__), "output", "replies")


NOTIFICATION_TEMPLATE = """\
This is an automated notification from the Email Deduplication System.

Your email has been identified as a potential duplicate:

  Your Email (Flagged):
    Message-ID:  {dup_message_id}
    Date Sent:   {dup_date}
    Subject:     {subject}

  Original Email on Record:
    Message-ID:  {orig_message_id}
    Date Sent:   {orig_date}

  Similarity Score: {similarity_score}%

If this was NOT a duplicate and you intended to send this email,
please reply with CONFIRM to restore it to active status.

No action is required if this is indeed a duplicate.
"""


def _build_eml(to_addr: str, subject: str, body: str, references: str) -> str:
    """Build a raw .eml string."""
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    lines = [
        "From: Enron Deduplication System <noreply@dedup.system>",
        f"To: {to_addr}",
        f"Subject: [Duplicate Notice] Re: {subject}",
        f"Date: {now}",
        f"References: {references}",
        "Content-Type: text/plain; charset=utf-8",
        "",
        body,
    ]
    return "\n".join(lines)


def generate_draft_emls(groups: list, similarity_map: dict) -> int:
    """
    Write .eml files for all duplicate groups (dry-run).
    Returns count of drafts written.
    """
    os.makedirs(REPLIES_DIR, exist_ok=True)
    count = 0
    for g in groups:
        dup  = g["duplicate"]
        orig = g["original"]

        sim = similarity_map.get(dup["message_id"], 0.0)
        body = NOTIFICATION_TEMPLATE.format(
            dup_message_id  = dup["message_id"],
            dup_date        = dup["date"],
            subject         = dup["subject"],
            orig_message_id = orig["message_id"],
            orig_date       = orig["date"],
            similarity_score= round(sim, 1),
        )

        eml_content = _build_eml(
            to_addr    = dup["from_address"],
            subject    = dup["subject"],
            body       = body,
            references = dup["message_id"],
        )

        # Safe filename from message_id
        safe_name = dup["message_id"].replace("/", "_").replace("<", "").replace(">", "")
        fname = os.path.join(REPLIES_DIR, f"{safe_name}.eml")
        with open(fname, "w", encoding="utf-8") as f:
            f.write(eml_content)
        count += 1
    return count


def send_via_gmail_api(groups: list, similarity_map: dict, conn, limit: int = None) -> dict:
    """
    Actually send notification emails via Gmail API directly.
    Logs results to output/send_log.csv.
    Returns stats dict.
    """
    import base64
    import json
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    import db as db_module

    SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
    BASE   = os.path.dirname(__file__)
    CREDS_FILE = os.path.join(BASE, "credentials.json")
    TOKEN_FILE  = os.path.join(BASE, "token.json")

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    service = build("gmail", "v1", credentials=creds)

    # Apply limit if specified
    if limit:
        groups = groups[:limit]

    os.makedirs(os.path.dirname(SEND_LOG_PATH), exist_ok=True)
    log_rows = []
    sent = 0
    failed = 0

    for g in groups:
        dup  = g["duplicate"]
        orig = g["original"]
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        sim = similarity_map.get(dup["message_id"], 0.0)
        body_text = NOTIFICATION_TEMPLATE.format(
            dup_message_id  = dup["message_id"],
            dup_date        = dup["date"],
            subject         = dup["subject"],
            orig_message_id = orig["message_id"],
            orig_date       = orig["date"],
            similarity_score= round(sim, 1),
        )

        mime_msg = MIMEText(body_text)
        mime_msg["to"]      = dup["from_address"]
        mime_msg["subject"] = f"[Duplicate Notice] Re: {dup['subject']}"
        raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode()

        status = "success"
        error  = ""
        try:
            service.users().messages().send(
                userId="me", body={"raw": raw}
            ).execute()
            db_module.mark_notification_sent(conn, dup["message_id"], now_ts)
            conn.commit()
            sent += 1
        except Exception as e:
            status = "failed"
            error  = str(e)
            failed += 1

        log_rows.append({
            "timestamp": now_ts,
            "recipient": dup["from_address"],
            "subject":   f"[Duplicate Notice] Re: {dup['subject']}",
            "status":    status,
            "error":     error,
        })

    # Write send log — check if file is empty (even if it exists) to decide on header
    write_header = (not os.path.exists(SEND_LOG_PATH)) or os.path.getsize(SEND_LOG_PATH) == 0
    with open(SEND_LOG_PATH, "a", newline="", encoding="utf-8") as f:
        fieldnames = ["timestamp", "recipient", "subject", "status", "error"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(log_rows)

    return {"sent": sent, "failed": failed}


def load_similarity_map() -> dict:
    """Load message_id -> similarity_score from duplicates_report.csv."""
    report_path = os.path.join(os.path.dirname(__file__), "output", "duplicates_report.csv")
    result = {}
    if not os.path.exists(report_path):
        return result
    with open(report_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                result[row["duplicate_message_id"]] = float(row["similarity_score"])
            except (KeyError, ValueError):
                pass
    return result
