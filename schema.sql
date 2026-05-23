-- Enron Email Pipeline Database Schema
-- SQLite

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Main emails table
CREATE TABLE IF NOT EXISTS emails (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id          TEXT UNIQUE NOT NULL,
    date                TEXT NOT NULL,              -- ISO8601 UTC
    from_address        TEXT NOT NULL,
    subject             TEXT NOT NULL DEFAULT '',
    body                TEXT,
    source_file         TEXT NOT NULL,
    x_from              TEXT,
    x_to                TEXT,
    x_cc                TEXT,
    x_bcc               TEXT,
    x_folder            TEXT,
    x_origin            TEXT,
    content_type        TEXT,
    has_attachment      INTEGER DEFAULT 0,          -- BOOLEAN (0/1)
    forwarded_content   TEXT,
    quoted_content      TEXT,
    headings            TEXT,
    is_duplicate        INTEGER DEFAULT 0,          -- BOOLEAN (0/1)
    duplicate_of        TEXT REFERENCES emails(message_id),
    notification_sent   INTEGER DEFAULT 0,          -- BOOLEAN (0/1)
    notification_date   TEXT
);

-- Normalized recipients table (to/cc/bcc)
CREATE TABLE IF NOT EXISTS email_recipients (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id        INTEGER NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    address         TEXT NOT NULL,
    recipient_type  TEXT NOT NULL CHECK(recipient_type IN ('to', 'cc', 'bcc'))
);

-- Parse failures log
CREATE TABLE IF NOT EXISTS parse_failures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    reason      TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_emails_date         ON emails(date);
CREATE INDEX IF NOT EXISTS idx_emails_from         ON emails(from_address);
CREATE INDEX IF NOT EXISTS idx_emails_subject      ON emails(subject);
CREATE INDEX IF NOT EXISTS idx_emails_is_duplicate ON emails(is_duplicate);
CREATE INDEX IF NOT EXISTS idx_recipients_email_id ON email_recipients(email_id);
CREATE INDEX IF NOT EXISTS idx_recipients_address  ON email_recipients(address);
