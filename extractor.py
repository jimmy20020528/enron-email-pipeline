"""
extractor.py — Parse raw Enron email files into structured dicts.
"""

import email
import email.utils
import os
import re
import chardet
from dateutil import parser as dateparser
from datetime import timezone


# ── Timezone abbreviation map (Enron data uses many non-standard abbrevs) ──
TZ_ABBREVS = {
    "PST": -8, "PDT": -7, "MST": -7, "MDT": -6,
    "CST": -6, "CDT": -5, "EST": -5, "EDT": -4,
    "GMT": 0,  "UTC": 0,  "BST": 1,  "CET": 1,
    "JST": 9,  "AEST": 10,
}


def _decode_bytes(raw: bytes) -> str:
    """Decode bytes to str, falling back to chardet detection."""
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, AttributeError):
            continue
    detected = chardet.detect(raw)
    enc = detected.get("encoding") or "latin-1"
    return raw.decode(enc, errors="replace")


def _parse_date(date_str: str):
    """Parse email date string → ISO8601 UTC string. Returns None on failure."""
    if not date_str:
        return None
    # Strip parenthetical tz abbrev like "(PST)" that confuse parsers
    cleaned = re.sub(r'\s*\([A-Z]{2,5}\)\s*$', '', date_str.strip())
    try:
        dt = dateparser.parse(cleaned, tzinfos={
            k: v * 3600 for k, v in TZ_ABBREVS.items()
        })
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def _parse_addresses(header_val: str) -> list:
    """Extract a list of clean email addresses from a header value."""
    if not header_val:
        return []
    # Handle multi-line continuations
    flat = re.sub(r'\r?\n\s+', ' ', header_val)
    parsed = email.utils.getaddresses([flat])
    result = []
    for _, addr in parsed:
        addr = addr.strip().lower()
        if addr and "@" in addr:
            result.append(addr)
    return result


def _split_body(raw_body: str):
    """
    Split raw body into:
      - primary body
      - forwarded_content (after -----Original Message----- or similar)
      - quoted_content (lines beginning with >)
    """
    forward_markers = [
        r'-{3,}\s*Original Message\s*-{3,}',
        r'-{3,}\s*Forwarded by',
        r'={3,}\s*Forwarded',
    ]
    forwarded = ""
    primary = raw_body

    for marker in forward_markers:
        m = re.search(marker, primary, re.IGNORECASE)
        if m:
            forwarded = primary[m.start():]
            primary = primary[:m.start()].strip()
            break

    # Extract quoted lines (>) from primary
    quoted_lines = []
    clean_lines = []
    for line in primary.splitlines():
        if line.startswith(">"):
            quoted_lines.append(line)
        else:
            clean_lines.append(line)

    body = "\n".join(clean_lines).strip()
    quoted = "\n".join(quoted_lines).strip()
    return body, forwarded.strip(), quoted


def _extract_headings(text: str) -> str:
    """Extract lines that look like headings (ALL CAPS short lines or markdown #)."""
    headings = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            headings.append(stripped)
        elif len(stripped) < 80 and stripped.isupper() and len(stripped) > 3:
            headings.append(stripped)
    return "\n".join(headings) if headings else ""


def parse_email_file(filepath: str, maildir_root: str) -> dict:
    """
    Parse a single email file.

    Returns a dict with all fields on success, or raises ValueError on
    failure to extract mandatory fields.
    """
    # Read raw bytes
    with open(filepath, "rb") as f:
        raw = f.read()

    text = _decode_bytes(raw)

    try:
        msg = email.message_from_string(text)
    except Exception as e:
        raise ValueError(f"email.message_from_string failed: {e}")

    # ── Mandatory fields ────────────────────────────────────────────────────
    message_id = (msg.get("Message-ID") or "").strip()
    if not message_id:
        raise ValueError("Missing Message-ID")

    date_raw = msg.get("Date") or ""
    date_utc = _parse_date(date_raw)
    if not date_utc:
        raise ValueError(f"Unparseable date: {date_raw!r}")

    from_raw = msg.get("From") or ""
    from_addresses = _parse_addresses(from_raw)
    if not from_addresses:
        raise ValueError(f"Missing/unparseable From: {from_raw!r}")
    from_address = from_addresses[0]

    to_raw = msg.get("To") or ""
    to_addresses = _parse_addresses(to_raw)
    # to_addresses can be empty — not a hard failure per spec

    subject = (msg.get("Subject") or "").strip()

    source_file = os.path.relpath(filepath, maildir_root)

    # ── Body extraction ─────────────────────────────────────────────────────
    raw_body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    raw_body += _decode_bytes(payload) + "\n"
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            raw_body = _decode_bytes(payload)
        elif isinstance(msg.get_payload(), str):
            raw_body = msg.get_payload()

    body, forwarded_content, quoted_content = _split_body(raw_body)
    headings = _extract_headings(body)

    # ── Optional fields ─────────────────────────────────────────────────────
    cc_addresses  = _parse_addresses(msg.get("Cc") or "")
    bcc_addresses = _parse_addresses(msg.get("Bcc") or "")

    x_from   = (msg.get("X-From")   or "").strip() or None
    x_to     = (msg.get("X-To")     or "").strip() or None
    x_cc     = (msg.get("X-cc")     or "").strip() or None
    x_bcc    = (msg.get("X-bcc")    or "").strip() or None
    x_folder = (msg.get("X-Folder") or "").strip() or None
    x_origin = (msg.get("X-Origin") or "").strip() or None

    content_type = (msg.get("Content-Type") or "").split(";")[0].strip() or None

    # Infer attachment presence
    has_attachment = 0
    if msg.is_multipart():
        for part in msg.walk():
            disp = part.get("Content-Disposition") or ""
            if "attachment" in disp.lower():
                has_attachment = 1
                break
    if not has_attachment and re.search(
        r'(see attached|attached (is|are|please find)|attachment)', body, re.IGNORECASE
    ):
        has_attachment = 1

    return {
        "message_id":       message_id,
        "date":             date_utc,
        "from_address":     from_address,
        "to_addresses":     to_addresses,
        "cc_addresses":     cc_addresses,
        "bcc_addresses":    bcc_addresses,
        "subject":          subject,
        "body":             body,
        "source_file":      source_file,
        "x_from":           x_from,
        "x_to":             x_to,
        "x_cc":             x_cc,
        "x_bcc":            x_bcc,
        "x_folder":         x_folder,
        "x_origin":         x_origin,
        "content_type":     content_type,
        "has_attachment":   has_attachment,
        "forwarded_content": forwarded_content or None,
        "quoted_content":   quoted_content or None,
        "headings":         headings or None,
    }


def discover_files(maildir_root: str, employees: list) -> list:
    """
    Walk maildir_root/<employee>/ for each employee in the list.
    Returns list of absolute file paths.
    """
    paths = []
    for emp in employees:
        emp_dir = os.path.join(maildir_root, emp)
        if not os.path.isdir(emp_dir):
            print(f"[WARN] Employee directory not found: {emp_dir}")
            continue
        for root, dirs, files in os.walk(emp_dir):
            # Skip hidden dirs
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if fname.startswith("."):
                    continue
                paths.append(os.path.join(root, fname))
    return paths
