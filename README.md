# Enron Email Pipeline

AI-assisted data extraction, deduplication, and notification pipeline built on the Enron Email Dataset.

## Architecture

```
maildir/ (raw email files)
    │
    ▼
extractor.py  ──────►  db.py (SQLite: enron.db)
(parse emails)          (normalize & store)
                             │
                             ▼
                      deduplicator.py
                      (fuzzy-match groups,
                       flag duplicates,
                       write report CSV)
                             │
                             ▼
                        notifier.py
                      (generate .eml drafts
                       or send via Gmail API)
                             │
                             ▼
                      gmail_mcp_server.py
                      (MCP server — exposes
                       send_email tool to
                       Claude Code)
```

## Prerequisites

- Python 3.10 or higher (tested on 3.11)
- A Google Cloud project with Gmail API enabled
- OAuth 2.0 Desktop credentials (`credentials.json`)

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Download and extract the Enron dataset

```bash
curl -O https://www.cs.cmu.edu/~enron/enron_mail_20150507.tar.gz
tar -xzf enron_mail_20150507.tar.gz
# Produces: maildir/
```

### 3. Configure Gmail credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Enable the **Gmail API** for your project
3. Create OAuth 2.0 credentials → **Desktop app**
4. Download `credentials.json` and place it in this project directory

### 4. Authorize Gmail (first run only)

```bash
python3 -c "
from google_auth_oauthlib.flow import InstalledAppFlow
SCOPES = ['https://www.googleapis.com/auth/gmail.send']
flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
creds = flow.run_local_server(port=0)
open('token.json', 'w').write(creds.to_json())
print('token.json saved')
"
```

A browser window will open for Google OAuth consent. After approving, `token.json` is saved and reused.

### 5. Configure MCP server (for Claude Code integration)

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "gmail-sender": {
      "type": "stdio",
      "command": "python3",
      "args": ["/absolute/path/to/gmail_mcp_server.py"],
      "env": {
        "GMAIL_CREDENTIALS": "/absolute/path/to/credentials.json",
        "GMAIL_TOKEN": "/absolute/path/to/token.json"
      }
    }
  }
}
```

See `mcp_config.json.example` for the full template.

## Running the Pipeline

### Full pipeline (dry-run)

```bash
python3 main.py --maildir ~/Downloads/maildir
```

Runs all four tasks: extraction → storage → dedup → draft .eml generation.

### Full pipeline with live email sending

```bash
python3 main.py --maildir ~/Downloads/maildir --send-live
```

### Custom employee selection

```bash
python3 main.py --maildir ~/Downloads/maildir \
  --employees lay-k skilling-j kean-s dasovich-j germany-c
```

### Skip extraction (reuse existing DB)

```bash
python3 main.py --skip-extract
```

## Selected Employee Mailboxes

| Employee | Role | Rationale |
|----------|------|-----------|
| `lay-k` | CEO Kenneth Lay | Highest-profile executive; largest email volume |
| `skilling-j` | COO Jeffrey Skilling | Second most senior; diverse correspondence |
| `kean-s` | VP Steven Kean | Heavy internal coordination; representative of management |
| `dasovich-j` | Government Affairs | External-facing emails; diverse subjects |
| `germany-c` | Manager | High volume; representative of operational staff |

These five mailboxes yielded **76,095 emails** — far exceeding the 10,000 minimum — spanning executive, management, and operational communication.

## Pipeline Results (Actual Run)

| Metric | Value |
|--------|-------|
| Email files discovered | 76,097 |
| Successfully parsed | 76,095 (99.997%) |
| Parse failures | 2 → `error_log.txt` |
| Extraction time | 59.6 seconds |
| Duplicate groups found | 20,665 |
| Emails flagged as duplicate | 45,091 (59.3%) |
| Average group size | 3.18 |
| Draft .eml notifications generated | 20,665 |
| Live notification emails sent | 2 (verified received) |

### MCP Notification — Verified Delivery

Claude Code called the `send_email` tool directly via the registered `gmail-sender` MCP server — no terminal command, just a native tool call from within the session:

```
Tool: mcp__gmail-sender__send_email
Args: { "to": "yucheng.yan.jimmy@gmail.com",
        "subject": "[Duplicate Notice] Re: Energy Issues",
        "body": "...real Enron message IDs, 98.53% similarity..." }
Result: {"status": "sent", "message_id": "19e56772e0acebed"}
```

![Duplicate notification email received in Gmail](email_screenshot.png)

---

## Output Files

> **Note**: `enron.db` is not included in this repository (376 MB). Run the pipeline to generate it locally (see Running the Pipeline above).

| File | Description |
|------|-------------|
| `enron.db` | SQLite database — generated by running the pipeline |
| `output/duplicates_report.csv` | All flagged duplicates with similarity scores |
| `output/replies/` | Draft `.eml` notification files (dry-run) |
| `output/send_log.csv` | Send results log (live mode) |
| `error_log.txt` | Parse failures with file paths and reasons |

## Database Schema

See `schema.sql`. Key tables:

- **`emails`** — one row per email; includes `is_duplicate`, `duplicate_of`, `notification_sent`, `notification_date`
- **`email_recipients`** — normalized to/cc/bcc (one row per address)
- **`parse_failures`** — files that failed parsing with reason

## Sample Queries

```bash
sqlite3 enron.db < sample_queries.sql
```

## Technical Choices

| Decision | Choice | Reason |
|----------|--------|--------|
| Fuzzy matching | `rapidfuzz` (`token_set_ratio`) | 5–10× faster than fuzzywuzzy; token_set handles email header noise |
| Date parsing | `python-dateutil` | Handles non-standard Enron timezone formats that `email.utils` rejects |
| Encoding | `chardet` fallback | Enron corpus mixes UTF-8 and Latin-1; prevents UnicodeDecodeError crashes |
| Database | SQLite (WAL mode) | Portable, zero-config, sufficient for 10K–500K emails |
| Dedup pre-filter | Group by `(from, subject)` before fuzzy match | Reduces O(n²) body comparisons to a few hundred per group |
