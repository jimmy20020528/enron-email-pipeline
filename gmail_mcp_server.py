"""
gmail_mcp_server.py — Minimal Gmail MCP server.

Exposes one tool: send_email(to, subject, body)
Run with: python3.11 gmail_mcp_server.py
"""

import base64
import json
import os
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from mcp.server.fastmcp import FastMCP

SCOPES         = ["https://www.googleapis.com/auth/gmail.send"]
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.environ.get("GMAIL_CREDENTIALS", os.path.join(BASE_DIR, "credentials.json"))
TOKEN_FILE       = os.environ.get("GMAIL_TOKEN",       os.path.join(BASE_DIR, "token.json"))

mcp = FastMCP("gmail-sender")


def _get_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


@mcp.tool()
def send_email(to: str, subject: str, body: str) -> str:
    """
    Send an email via Gmail API.

    Args:
        to:      Recipient email address.
        subject: Email subject line.
        body:    Plain-text email body.

    Returns:
        JSON string with status and Gmail message ID.
    """
    service = _get_service()
    message = MIMEText(body)
    message["to"]      = to
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    result = service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()
    return json.dumps({"status": "sent", "message_id": result["id"]})


if __name__ == "__main__":
    mcp.run(transport="stdio")
