-- sample_queries.sql
-- Demonstrates correct DB operation for the Enron Email Pipeline
-- Run: sqlite3 enron.db < sample_queries.sql


-- ── Query 1: Total emails per sender (top 20) ───────────────────────────────
-- Shows which employees sent the most emails in the dataset.
--
-- Expected output: A ranked list of email addresses with their total sent count
-- and how many of those were flagged as duplicates. Example:
--   from_address                       | total_sent | duplicates_sent
--   jeff.dasovich@enron.com            |  2,542     |  1,201
--   richard.shapiro@enron.com          |  1,863     |    874
--   ...
-- Demonstrates: GROUP BY aggregation, SUM on boolean column, ORDER BY DESC

SELECT
    from_address,
    COUNT(*)          AS total_sent,
    SUM(is_duplicate) AS duplicates_sent
FROM emails
GROUP BY from_address
ORDER BY total_sent DESC
LIMIT 20;


-- ── Query 2: Emails in a date range ─────────────────────────────────────────
-- Find all emails sent during the Enron collapse (Oct–Dec 2001).
--
-- Expected output: Rows of emails within the specified date range, ordered
-- chronologically. Example:
--   message_id                          | date                  | from_address              | subject
--   <xyz.JavaMail.evans@thyme>          | 2001-10-01T08:23:00Z  | kenneth.lay@enron.com     | Energy Policy Update
--   ...
-- Demonstrates: date range filtering using ISO8601 UTC strings, index on date

SELECT
    message_id,
    date,
    from_address,
    subject
FROM emails
WHERE date BETWEEN '2001-10-01T00:00:00Z' AND '2001-12-31T23:59:59Z'
ORDER BY date ASC
LIMIT 50;


-- ── Query 3: Emails that have CC recipients ──────────────────────────────────
-- Joins the normalized recipients table to find emails with at least one CC.
--
-- Expected output: Emails with the highest CC recipient counts. Example:
--   message_id               | date                  | from_address           | subject          | cc_count
--   <abc.JavaMail...>        | 2001-06-15T14:00:00Z  | jeff.skilling@enron.com| Q2 Results       | 47
--   ...
-- Demonstrates: normalized JOIN on email_recipients, GROUP BY with COUNT,
-- proves to/cc/bcc are stored in a separate table (not comma-separated strings)

SELECT
    e.message_id,
    e.date,
    e.from_address,
    e.subject,
    COUNT(r.id) AS cc_count
FROM emails e
JOIN email_recipients r
    ON r.email_id = e.id AND r.recipient_type = 'cc'
GROUP BY e.id
ORDER BY cc_count DESC
LIMIT 20;


-- ── Query 4: All flagged duplicates with originals ───────────────────────────
-- Full duplicate report joined with original email metadata.
--
-- Expected output: Each duplicate email paired with its original. Example:
--   duplicate_id      | duplicate_date        | from_address          | subject      | original_id      | original_date         | notification_sent
--   <dup.JavaMail...> | 2002-01-15T09:00:00Z  | jeff.dasovich@enron   | Daily Call   | <orig.JavaMail...>| 2001-02-27T11:30:00Z | 0
--   ...
-- Demonstrates: self-join on emails table using duplicate_of FK,
-- is_duplicate flag, notification_sent tracking

SELECT
    d.message_id          AS duplicate_id,
    d.date                AS duplicate_date,
    d.from_address,
    d.subject,
    o.message_id          AS original_id,
    o.date                AS original_date,
    d.notification_sent
FROM emails d
JOIN emails o ON o.message_id = d.duplicate_of
WHERE d.is_duplicate = 1
ORDER BY d.date DESC
LIMIT 50;


-- ── Query 5: Parse failure summary ──────────────────────────────────────────
-- Shows the most common reasons emails failed to parse.
--
-- Expected output: Failure reasons grouped by type with occurrence count.
-- Example:
--   reason                                             | occurrences
--   Missing/unparseable From: '<"d@piassick"...>'      | 1
--   Missing/unparseable From: 'pep <performance.>'     | 1
-- Demonstrates: parse_failures table is populated, error logging works correctly

SELECT
    reason,
    COUNT(*) AS occurrences
FROM parse_failures
GROUP BY reason
ORDER BY occurrences DESC
LIMIT 20;
