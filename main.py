#!/usr/bin/env python3
"""
main.py — Entry point for the Enron email pipeline.

Usage:
    python3.11 main.py [--maildir PATH] [--employees e1 e2 ...] [--send-live]

Default employees: lay-k, skilling-j, kean-s, dasovich-j, germany-c
"""

import argparse
import os
import sys
import time

import db as db_module
import extractor
import deduplicator
import notifier

# ── Default configuration ────────────────────────────────────────────────────
DEFAULT_MAILDIR   = os.path.expanduser("~/Downloads/maildir")
DEFAULT_EMPLOYEES = ["lay-k", "skilling-j", "kean-s", "dasovich-j", "germany-c"]
DB_PATH           = os.path.join(os.path.dirname(__file__), "enron.db")
ERROR_LOG_PATH    = os.path.join(os.path.dirname(__file__), "error_log.txt")
BATCH_SIZE        = 500   # commit every N emails


def parse_args():
    p = argparse.ArgumentParser(description="Enron Email Pipeline")
    p.add_argument("--maildir",   default=DEFAULT_MAILDIR,
                   help="Path to extracted maildir folder")
    p.add_argument("--employees", nargs="+", default=DEFAULT_EMPLOYEES,
                   help="Employee folder names to process")
    p.add_argument("--send-live", action="store_true",
                   help="Actually send notification emails via Gmail")
    p.add_argument("--skip-extract", action="store_true",
                   help="Skip extraction (use existing DB)")
    p.add_argument("--skip-dedup",   action="store_true",
                   help="Skip duplicate detection")
    p.add_argument("--test-recipient", default=None,
                   help="Override all notification recipients with this email (for testing)")
    return p.parse_args()


def run_extraction(args, conn):
    print("\n" + "="*60)
    print("TASK 1 — Data Extraction")
    print("="*60)

    files = extractor.discover_files(args.maildir, args.employees)
    total = len(files)
    print(f"Found {total:,} email files across {len(args.employees)} mailboxes")
    print(f"Employees: {', '.join(args.employees)}\n")

    success = 0
    failed  = 0
    skipped = 0   # duplicate message_id
    t0 = time.time()

    with open(ERROR_LOG_PATH, "w", encoding="utf-8") as err_f:
        err_f.write("source_file\treason\n")

        for i, fpath in enumerate(files, 1):
            try:
                record = extractor.parse_email_file(fpath, args.maildir)
                row_id = db_module.insert_email(conn, record)
                if row_id is None:
                    skipped += 1
                else:
                    success += 1
            except Exception as e:
                reason = str(e)
                rel_path = os.path.relpath(fpath, args.maildir)
                err_f.write(f"{rel_path}\t{reason}\n")
                try:
                    db_module.log_failure(conn, rel_path, reason)
                except Exception:
                    pass
                failed += 1

            if i % BATCH_SIZE == 0:
                conn.commit()
                elapsed = time.time() - t0
                pct = i / total * 100
                print(f"  [{pct:5.1f}%] {i:,}/{total:,}  "
                      f"ok={success:,}  fail={failed:,}  skip={skipped:,}  "
                      f"({elapsed:.0f}s)")

        conn.commit()

    elapsed = time.time() - t0
    print(f"\n--- Extraction complete ({elapsed:.1f}s) ---")
    print(f"  Total files  : {total:,}")
    print(f"  Parsed OK    : {success:,}")
    print(f"  Failed       : {failed:,}  → see error_log.txt")
    print(f"  Duplicates   : {skipped:,}  (duplicate message_id, skipped)")

    completeness = db_module.get_field_completeness(conn)

    print("\n--- Mandatory field completeness ---")
    for field, pct in completeness["_mandatory"].items():
        flag = "✓" if pct == 100.0 else "!"
        print(f"  {flag} {field:<25} {pct:5.1f}%")

    print("\n--- Optional field completeness ---")
    for field, pct in sorted(completeness["_optional"].items(), key=lambda x: -x[1]):
        print(f"    {field:<25} {pct:5.1f}%")

    return success


def run_deduplication(conn):
    print("\n" + "="*60)
    print("TASK 3 — Duplicate Detection")
    print("="*60)

    t0 = time.time()
    groups = deduplicator.find_duplicates(conn)
    stats  = deduplicator.flag_duplicates(conn, groups)
    elapsed = time.time() - t0

    print(f"\n--- Deduplication complete ({elapsed:.1f}s) ---")
    print(f"  Duplicate groups found : {stats['total_groups']:,}")
    print(f"  Emails flagged         : {stats['total_flagged']:,}")
    print(f"  Avg group size         : {stats['avg_group_size']}")
    print(f"  Report written         : output/duplicates_report.csv")

    return groups


def run_notifications(conn, send_live: bool, test_recipient: str = None):
    print("\n" + "="*60)
    print("TASK 4 — Notifications")
    print("="*60)

    groups      = deduplicator.get_duplicate_groups_for_notification(conn)
    sim_map     = notifier.load_similarity_map()

    # Override recipients for testing
    if test_recipient:
        for g in groups:
            g["duplicate"] = dict(g["duplicate"])
            g["duplicate"]["from_address"] = test_recipient
        print(f"\n  [TEST MODE] All notifications → {test_recipient}")

    # Always generate .eml drafts
    draft_count = notifier.generate_draft_emls(groups, sim_map)
    print(f"\n  Draft .eml files written : {draft_count}  → output/replies/")

    if send_live:
        limit = 1 if test_recipient else None
        print("\n  Sending live emails via Gmail API...")
        result = notifier.send_via_gmail_api(groups, sim_map, conn, limit=limit)
        print(f"  Sent    : {result['sent']}")
        print(f"  Failed  : {result['failed']}")
        print(f"  Log     : output/send_log.csv")
    else:
        print("\n  (Dry-run mode — use --send-live to actually send emails)")


def main():
    args = parse_args()

    # Validate maildir
    if not args.skip_extract and not os.path.isdir(args.maildir):
        print(f"[ERROR] maildir not found: {args.maildir}")
        print("  Download: https://www.cs.cmu.edu/~enron/enron_mail_20150507.tar.gz")
        sys.exit(1)

    print("\nEnron Email Pipeline")
    print(f"  DB       : {DB_PATH}")
    print(f"  Maildir  : {args.maildir}")
    print(f"  Mode     : {'LIVE SEND' if args.send_live else 'dry-run'}")

    # Init DB
    db_module.init_db(DB_PATH)
    conn = db_module.get_connection(DB_PATH)

    try:
        if not args.skip_extract:
            run_extraction(args, conn)

        if not args.skip_dedup:
            run_deduplication(conn)

        run_notifications(conn, send_live=args.send_live, test_recipient=args.test_recipient)

    finally:
        conn.close()

    print("\n Pipeline complete.\n")


if __name__ == "__main__":
    main()
