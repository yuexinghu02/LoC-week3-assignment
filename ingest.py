"""Load a books CSV file into books.db, following the rules in rules.md.

Usage:
    python ingest.py <file.csv>

books.db and ingestion-log.json are kept next to this script (Decision 32).
Only the Python standard library is used and nothing goes over the network,
so the same file loaded into the same database always gives the same result.
"""

import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "books.db"
LOG_PATH = HERE / "ingestion-log.json"

EXPECTED_COLUMNS = [
    "book_id", "goodreads_book_id", "best_book_id", "work_id", "books_count",
    "isbn", "isbn13", "authors", "original_publication_year", "original_title",
    "title", "language_code", "average_rating", "ratings_count",
    "work_ratings_count", "work_text_reviews_count", "ratings_1", "ratings_2",
    "ratings_3", "ratings_4", "ratings_5", "image_url", "small_image_url",
]

# Column kinds (Decision 22).
ID_COLUMNS = ["work_id", "goodreads_book_id", "book_id", "best_book_id"]
DESCRIPTIVE_COLUMNS = ["title", "authors", "original_publication_year", "original_title",
                       "language_code", "image_url", "small_image_url"]
STAR_COLUMNS = [f"ratings_{i}" for i in range(1, 6)]
COUNT_COLUMNS = ["books_count", "work_text_reviews_count", "ratings_count"] + STAR_COLUMNS
ISBN_GROUPS = {
    "isbn": ["isbn", "isbn_status", "isbn_original"],
    "isbn13": ["isbn13", "isbn13_status", "isbn13_original"],
    "other_id": ["other_id"],
}

# Warning groups (Decision 30).
DECISION, LOOKUP, CHECK = "needs_decision", "needs_lookup", "check_this"

# Worst first, for the row summary status (Decision 9).
STATUS_RANK = {"rejected": 4, "inferred": 3, "repaired": 2, "ok": 1, "empty": 0}

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    book_key INTEGER PRIMARY KEY,
    work_id, goodreads_book_id, best_book_id, book_id, books_count,
    isbn TEXT, isbn_status TEXT, isbn_original TEXT,
    isbn13 TEXT, isbn13_status TEXT, isbn13_original TEXT,
    other_id TEXT, isbn_row_status TEXT,
    authors TEXT, original_publication_year TEXT, original_title TEXT, title TEXT,
    language_code TEXT, average_rating, ratings_count, work_ratings_count,
    work_text_reviews_count, ratings_1, ratings_2, ratings_3, ratings_4, ratings_5,
    image_url TEXT, small_image_url TEXT,
    created_run INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS books_work_id ON books(work_id)
    WHERE typeof(work_id) = 'integer';
CREATE INDEX IF NOT EXISTS books_goodreads_book_id ON books(goodreads_book_id);
CREATE INDEX IF NOT EXISTS books_book_id ON books(book_id);
CREATE INDEX IF NOT EXISTS books_isbn ON books(isbn);
CREATE INDEX IF NOT EXISTS books_isbn13 ON books(isbn13);

CREATE TABLE IF NOT EXISTS runs (
    run INTEGER PRIMARY KEY,
    started_at TEXT, finished_at TEXT, status TEXT,
    file_given TEXT, file_path TEXT, file_size INTEGER, file_sha256 TEXT,
    rows_read INTEGER, loaded INTEGER, updated INTEGER,
    skipped_already_exist INTEGER, rejected INTEGER
);
CREATE TABLE IF NOT EXISTS book_sources (
    run INTEGER, book_key INTEGER, line INTEGER, action TEXT
);
CREATE TABLE IF NOT EXISTS history (
    run INTEGER, book_key INTEGER, column_name TEXT,
    old_value TEXT, new_value TEXT, reason TEXT
);
CREATE TABLE IF NOT EXISTS warnings (
    run INTEGER, book_key INTEGER, line INTEGER, warning_group TEXT,
    column_name TEXT, value TEXT, reason TEXT, action TEXT
);
"""


class RunFailed(Exception):
    """The file can't be used at all, so nothing is changed (Decision 4)."""


# ---------------------------------------------------------------------------
# ISBN rules (Rules 2-8, Decisions 7-15)
# ---------------------------------------------------------------------------

LABEL_RE = re.compile(r"^\s*(?:urn\s*:\s*)?isbn(?:-?1[03])?\s*:?\s*", re.IGNORECASE)
FORMAT_LABEL_RE = re.compile(r"\s*\(\s*[A-Za-z][A-Za-z .-]*\)\s*$")
SCI_RE = re.compile(r"\d+(?:\.\d+)?[eE]\+?\d+")
DOT_ZERO_RE = re.compile(r"(\d+)\.0+")


def isbn10_valid(s):
    if not re.fullmatch(r"\d{9}[\dX]", s):
        return False
    return sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s)) % 11 == 0


def isbn13_checksum_ok(s):
    if not re.fullmatch(r"\d{13}", s):
        return False
    return sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s)) % 10 == 0


def is_ismn(s):
    """979-0 numbers are ISMNs for printed music, not ISBNs (Rule 3)."""
    return s.startswith("9790")


def isbn13_valid(s):
    return isbn13_checksum_ok(s) and s[:3] in ("978", "979") and not is_ismn(s)


def isbn13_from_isbn10(isbn10):
    core = "978" + isbn10[:9]
    check = (10 - sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(core)) % 10) % 10
    return core + str(check)


def isbn10_from_isbn13(isbn13):
    core = isbn13[3:12]
    check = (11 - sum((10 - i) * int(c) for i, c in enumerate(core)) % 11) % 11
    return core + ("X" if check == 10 else str(check))


def round_to_ten(n):
    """How the source rounded: to the nearest 10, with ties to the even ten (Decision 10)."""
    return int((Decimal(n) / 10).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)) * 10


def upc_valid(s):
    if not re.fullmatch(r"\d{12}", s):
        return False
    return (sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(s[:11])) + int(s[11])) % 10 == 0


def strip_formatting(raw):
    """Remove labels, spaces and hyphens (Rule 5, Decisions 7-8)."""
    fixes = []
    text = raw
    if LABEL_RE.match(text):
        text = LABEL_RE.sub("", text, count=1)
        fixes.append('removed an "ISBN" label')
    if FORMAT_LABEL_RE.search(text):
        text = FORMAT_LABEL_RE.sub("", text)
        fixes.append('removed a format label like "(hardback)"')
    if re.search(r"\s", text):
        fixes.append("removed spaces")
    if "-" in text:
        fixes.append("removed hyphens")
    return re.sub(r"[\s-]", "", text), fixes


def read_isbn10(raw):
    if raw.strip() == "":
        return {"kind": "empty"}
    text, fixes = strip_formatting(raw)
    if re.fullmatch(r"\d{13}", text):
        return {"kind": "isbn13", "digits": text, "fixes": fixes}
    if text.endswith("x") and text[:-1].isdigit():
        text = text[:-1] + "X"
        fixes.append('changed "x" to "X"')
    if len(text) < 10 and re.fullmatch(r"\d*[\dX]", text):
        padded = text.zfill(10)
        if not isbn10_valid(padded):
            return {"kind": "rejected",
                    "reason": "fails the ISBN-10 checksum, even with the missing leading zeros added back"}
        missing = 10 - len(text)
        fixes.append(f"added {missing} missing leading zero{'s' if missing > 1 else ''}")
        text = padded
    if re.fullmatch(r"\d{9}[\dX]", text):
        if isbn10_valid(text):
            return {"kind": "valid", "value": text, "status": "repaired" if fixes else "ok", "fixes": fixes}
        return {"kind": "rejected", "reason": "wrong check digit (fails the ISBN-10 checksum)"}
    if text.isdigit():
        return {"kind": "rejected", "reason": f"{len(text)} digits; an ISBN-10 has 10"}
    return {"kind": "rejected",
            "reason": "has characters that can't be in an ISBN-10 (only digits, and X as the last character)"}


def read_isbn13(raw):
    if raw.strip() == "":
        return {"kind": "empty"}
    text, fixes = strip_formatting(raw)
    if text.isdigit():
        digits = text
    elif DOT_ZERO_RE.fullmatch(text):
        digits = DOT_ZERO_RE.fullmatch(text).group(1)
        fixes.append('converted from a number ending in ".0"')
    elif SCI_RE.fullmatch(text):
        value = Decimal(text)
        if value != value.to_integral_value():
            return {"kind": "rejected", "reason": "a number in scientific notation that isn't a whole number"}
        digits = str(int(value))
        if len(digits) == 13:
            # Rounded to 12 significant digits, so the last digit is lost (Decision 10).
            return {"kind": "rounded", "number": int(digits), "fixes": fixes}
        fixes.append("converted from scientific notation")
    else:
        return {"kind": "rejected",
                "reason": "has characters that can't be in an ISBN-13 (only digits)"}
    if len(digits) == 13:
        return {"kind": "exact13", "digits": digits, "fixes": fixes}
    if len(digits) > 13:
        return {"kind": "rejected", "reason": f"{len(digits)} digits; an ISBN-13 has 13"}
    return {"kind": "short", "digits": digits, "fixes": fixes}


def clean_isbns(raw10, raw13):
    """Clean one row's two ISBN columns.

    Returns the cleaned fields, warnings as (group, column, value, reason, action),
    and the repairs that need no action (counted in "handled automatically").
    """
    out = {"isbn": None, "isbn_status": "empty", "isbn_original": raw10 if raw10.strip() else None,
           "isbn13": None, "isbn13_status": "empty", "isbn13_original": raw13 if raw13.strip() else None,
           "other_id": None}
    notes, auto = [], []

    r10 = read_isbn10(raw10)
    r13 = read_isbn13(raw13)

    # An ISBN-13 in the ISBN-10 column: move it, then calculate the ISBN-10 (Rule 8).
    if r10["kind"] == "isbn13":
        digits = r10["digits"]
        same_as_13 = r13["kind"] == "empty" or (r13["kind"] == "exact13" and r13["digits"] == digits)
        if isbn13_valid(digits) and same_as_13:
            out["isbn13"], out["isbn13_status"] = digits, "repaired"
            if digits.startswith("978"):
                out["isbn"], out["isbn_status"] = isbn10_from_isbn13(digits), "repaired"
            notes.append((CHECK, "isbn", raw10, "an ISBN-13 was in the ISBN-10 column",
                          "moved it to isbn13 and calculated the ISBN-10 from it"))
            out["isbn_row_status"] = row_status(out)
            return out, notes, auto
        if not isbn13_valid(digits):
            reason = "13 digits, and not a valid ISBN-13 either"
        else:
            reason = "an ISBN-13 in the ISBN-10 column, but the isbn13 column already has a different value"
        r10 = {"kind": "rejected", "reason": reason}

    reason10 = reason13 = None
    if r10["kind"] == "valid":
        out["isbn"], out["isbn_status"] = r10["value"], r10["status"]
        auto += [f"ISBN-10: {fix}" for fix in r10["fixes"]]
    elif r10["kind"] == "rejected":
        out["isbn_status"], reason10 = "rejected", r10["reason"]
    isbn10 = out["isbn"]

    def from_isbn10(note=None, done="ISBN-13 calculated from the ISBN-10"):
        out["isbn13"], out["isbn13_status"] = isbn13_from_isbn10(isbn10), "repaired"
        if note:
            notes.append(note)
        else:
            auto.append(done)

    def mismatch(old):
        calculated = isbn13_from_isbn10(isbn10)
        from_isbn10((DECISION, "isbn13", raw13, f"ISBN-10 {isbn10} and ISBN-13 {old} don't match",
                     f"kept the ISBN-10 and calculated ISBN-13 {calculated} from it; "
                     "the old ISBN-13 is kept as the original value. Decide which is right"))

    def reject13(reason, other_id=None):
        nonlocal reason13
        if other_id is not None:
            out["other_id"] = other_id
        if isbn10:
            if other_id is not None:
                auto.append("moved a non-ISBN code to other_id")
                from_isbn10()
            else:
                from_isbn10((CHECK, "isbn13", raw13, reason, "calculated the ISBN-13 from the ISBN-10"))
        else:
            out["isbn13_status"], reason13 = "rejected", reason

    kind = r13["kind"]
    if kind == "empty":
        if isbn10:
            from_isbn10()
    elif kind == "rejected":
        reject13(r13["reason"])
    elif kind == "exact13":
        digits = r13["digits"]
        if digits[:3] not in ("978", "979"):
            reject13("not an ISBN (doesn't start with 978 or 979); moved to other_id", other_id=digits)
        elif is_ismn(digits):
            reject13("an ISMN for printed music (starts with 9790), not an ISBN; moved to other_id", other_id=digits)
        elif not isbn13_checksum_ok(digits):
            reject13("wrong check digit (fails the ISBN-13 checksum)")
        elif isbn10 and isbn13_from_isbn10(isbn10) != digits:
            mismatch(digits)
        else:
            out["isbn13"], out["isbn13_status"] = digits, "repaired" if r13["fixes"] else "ok"
            auto += [f"ISBN-13: {fix}" for fix in r13["fixes"]]
    elif kind == "short":
        digits = r13["digits"]
        if isbn10 and "X" not in isbn10 and digits.lstrip("0") == isbn10.lstrip("0"):
            from_isbn10((CHECK, "isbn13", raw13, "the ISBN-10 was in the ISBN-13 column",
                         "calculated the ISBN-13 from the ISBN-10"))
        elif len(digits) in (11, 12) and upc_valid(digits.zfill(12)):
            reject13("a UPC barcode, not an ISBN; moved to other_id", other_id=digits.zfill(12))
        else:
            reject13(f"only {len(digits)} digits; an ISBN-13 has 13, so it's cut short or isn't an ISBN")
    elif kind == "rounded":
        number = r13["number"]
        if str(number)[:3] not in ("978", "979"):
            reject13("not an ISBN (doesn't start with 978 or 979); moved to other_id",
                     other_id=raw13.strip())
        elif is_ismn(str(number)):
            reject13("an ISMN for printed music (starts with 9790), not an ISBN; moved to other_id",
                     other_id=raw13.strip())
        elif isbn10:
            if round_to_ten(int(isbn13_from_isbn10(isbn10))) == number:
                from_isbn10(done="rebuilt a rounded ISBN-13 from the ISBN-10")
            else:
                mismatch(raw13.strip())
        else:
            fits = [str(n) for n in range(number - 5, number + 6)
                    if isbn13_valid(str(n)) and round_to_ten(n) == number]
            if len(fits) == 1:
                out["isbn13"], out["isbn13_status"] = fits[0], "inferred"
                notes.append((CHECK, "isbn13", raw13,
                              "rounded, with no usable ISBN-10, and only one valid ISBN-13 fits",
                              f"filled in {fits[0]} as inferred; it's only right if the original was a real ISBN"))
            elif fits:
                reject13(f"rounded, with no usable ISBN-10, and {len(fits)} valid ISBN-13s fit: {', '.join(fits)}")
            else:
                reject13("rounded, with no usable ISBN-10, and no valid ISBN-13 fits")

    # An empty or rejected ISBN-10 is calculated from a valid ISBN-13,
    # but never from an inferred one (Rule 6, Decisions 13-14).
    if out["isbn"] is None and out["isbn13_status"] in ("ok", "repaired") and out["isbn13"].startswith("978"):
        out["isbn"], out["isbn_status"] = isbn10_from_isbn13(out["isbn13"]), "repaired"
        if reason10:
            notes.append((CHECK, "isbn", raw10, reason10, "calculated the ISBN-10 from the valid ISBN-13"))
            reason10 = None
        else:
            auto.append("ISBN-10 calculated from the ISBN-13")

    if out["isbn_status"] == "rejected":
        notes.append((LOOKUP, "isbn", raw10, reason10, "find the real ISBN-10"))
    if out["isbn13_status"] == "rejected":
        notes.append((LOOKUP, "isbn13", raw13, reason13, "find the real ISBN-13"))
    out["isbn_row_status"] = row_status(out)
    return out, notes, auto


def row_status(values):
    return max(values["isbn_status"], values["isbn13_status"], key=STATUS_RANK.get)


# ---------------------------------------------------------------------------
# Other columns
# ---------------------------------------------------------------------------

def whole(text):
    """A whole number, or None. Used for IDs and counts (Decision 4)."""
    if text is None:
        return None
    text = str(text).strip()
    return int(text) if re.fullmatch(r"\d+", text) else None


def as_stored(text):
    """IDs and counts: whole numbers are stored as numbers, anything else as given."""
    if text is None or text.strip() == "":
        return None
    number = whole(text)
    return number if number is not None else text


def as_text(text):
    return None if text is None or text.strip() == "" else text


def same(a, b):
    return (None if a is None else str(a)) == (None if b is None else str(b))


def recalculate(stars):
    """work_ratings_count and average_rating from the star counts (Decision 23)."""
    counts = [whole(s) for s in stars]
    if any(c is None for c in counts) or sum(counts) == 0:
        return None
    total = sum(counts)
    weighted = sum((i + 1) * c for i, c in enumerate(counts))
    average = (Decimal(weighted) / Decimal(total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return total, average


def as_decimal(text):
    try:
        return Decimal(str(text).strip())
    except (InvalidOperation, ValueError):
        return None


def normalize_title(title):
    """Rule 10: lowercase, no series tag like "(Harry Potter, #1)", single spaces."""
    title = re.sub(r"\s*\([^()]*#[^()]*\)\s*$", "", title or "")
    return re.sub(r"\s+", " ", title).strip().lower()


def first_author(authors):
    return (authors or "").split(",")[0].strip().lower()


# ---------------------------------------------------------------------------
# Reading the file and the log
# ---------------------------------------------------------------------------

def read_log():
    if not LOG_PATH.exists():
        return []
    try:
        log = json.loads(LOG_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RunFailed(f"{LOG_PATH.name} can't be read ({e})")
    if not isinstance(log, list):
        raise RunFailed(f"{LOG_PATH.name} isn't a JSON array")
    return log


def write_log(log):
    """Write the whole log to a temporary file, then swap it in, so a crash can't leave half a file."""
    fd, tmp = tempfile.mkstemp(dir=HERE, prefix=".ingestion-log-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(log, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, LOG_PATH)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def read_csv(path):
    """Returns (file details, header, [(line, fields)]). Blank lines aren't rows."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise RunFailed(f"file not found: {path}")
    except OSError as e:
        raise RunFailed(f"file can't be read: {e}")
    details = {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise RunFailed(f"the file isn't UTF-8 text ({e})")
    reader = csv.reader(text.splitlines(keepends=True))
    records = []
    try:
        header = next(reader, None)
        if header is None:
            raise RunFailed("the file is empty")
        last_line = reader.line_num
        for fields in reader:
            start = last_line + 1
            last_line = reader.line_num
            if fields:
                records.append((start, fields))
    except csv.Error as e:
        raise RunFailed(f"the CSV can't be parsed near line {reader.line_num}: {e}")
    missing = [c for c in EXPECTED_COLUMNS if c not in header]
    if missing:
        raise RunFailed(f"the header is missing {len(missing)} expected column(s): {', '.join(missing)}")
    return details, header, records


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

class Load:
    def __init__(self, con, run):
        self.con = con
        self.run = run
        self.rejected_rows = []
        self.warnings = {DECISION: [], LOOKUP: [], CHECK: []}
        self.auto = Counter()
        self.changes = []
        self.counts = Counter()
        self.isbn_statuses = {"isbn": Counter(), "isbn13": Counter()}
        self.touched = {}  # book_key -> (line, book_id, title)
        self.saw_rounded = False

    def warn(self, group, line, book_key, book_id, title, column, value, reason, action):
        self.warnings[group].append({"line": line, "book_id": book_id, "title": title, "column": column,
                                     "value": value, "reason": reason, "action": action})
        self.con.execute("INSERT INTO warnings VALUES (?,?,?,?,?,?,?,?)",
                         (self.run, book_key, line, group, column, value, reason, action))

    def reject(self, line, book_id, title, reason):
        self.rejected_rows.append({"line": line, "book_id": book_id, "title": title, "reason": reason})

    def load(self, header, records):
        index = {}
        for i, name in enumerate(header):
            index.setdefault(name, i)
        self.counts["rows_read"] = len(records)

        # Whole-row problems first: wrong number of fields, exact duplicates (Decisions 1, 2, 17).
        accepted, first_seen = [], {}
        for line, fields in records:
            get = lambda c: fields[index[c]] if index[c] < len(fields) else ""
            if len(fields) != len(header):
                self.reject(line, get("book_id"), get("title"),
                            f"has {len(fields)} fields; the header has {len(header)}")
                continue
            key = tuple(fields)
            if key in first_seen:
                self.reject(line, get("book_id"), get("title"), f"exact duplicate of line {first_seen[key]}")
                continue
            first_seen[key] = line
            accepted.append((line, {c: fields[index[c]] for c in EXPECTED_COLUMNS}))

        # The same work_id twice with different values: reject every copy (Decision 18).
        by_work = defaultdict(list)
        for line, rec in accepted:
            work_id = whole(rec["work_id"])
            if work_id is not None:
                by_work[work_id].append((line, rec))
        conflicting = set()
        for work_id, copies in by_work.items():
            if len(copies) < 2:
                continue
            lines = [line for line, _ in copies]
            differ = [c for c in EXPECTED_COLUMNS if len({rec[c] for _, rec in copies}) > 1]
            for line, rec in copies:
                others = ", ".join(str(n) for n in lines if n != line)
                reason = (f"work_id {work_id} is also on line {others} with different values "
                          f"({', '.join(differ)})")
                self.reject(line, rec["book_id"], rec["title"], reason)
                self.warn(DECISION, line, None, rec["book_id"], rec["title"], "work_id", str(work_id), reason,
                          "fix the file so the book appears once, then load it again")
                conflicting.add(line)

        for line, rec in accepted:
            if line not in conflicting:
                self.load_row(line, rec)

        self.check_shared_isbns()

    def load_row(self, line, rec):
        cleaned, notes, auto = clean_isbns(rec["isbn"], rec["isbn13"])
        self.auto.update(auto)
        self.isbn_statuses["isbn"][cleaned["isbn_status"]] += 1
        self.isbn_statuses["isbn13"][cleaned["isbn13_status"]] += 1
        if read_isbn13(rec["isbn13"])["kind"] == "rounded":
            self.saw_rounded = True

        ids = {}
        for col in ID_COLUMNS:
            ids[col] = whole(rec[col])
            if ids[col] is None and rec[col].strip():
                notes.append((CHECK, col, rec[col], f"{col} isn't a whole number, so it isn't used for matching",
                              "check the value"))

        book_key, match_notes = self.find_book(rec, cleaned, ids)
        notes += match_notes
        if book_key is None:
            book_key = self.insert_book(rec, cleaned, ids, notes)
            action = "created"
            self.counts["loaded"] += 1
        elif self.update_book(book_key, line, rec, cleaned, ids, notes):
            action = "updated"
            self.counts["updated"] += 1
        else:
            action = "unchanged"
            self.counts["skipped_already_exist"] += 1
        self.con.execute("INSERT INTO book_sources VALUES (?,?,?,?)", (self.run, book_key, line, action))
        self.touched[book_key] = (line, rec["book_id"], rec["title"])
        for group, column, value, reason, what in notes:
            self.warn(group, line, book_key, rec["book_id"], rec["title"], column, value, reason, what)

    def one_book(self, column, value):
        rows = self.con.execute(f"SELECT book_key FROM books WHERE {column} = ?", (value,)).fetchall()
        return rows[0][0] if len(rows) == 1 else None

    def find_book(self, rec, cleaned, ids):
        """Rule 9 and Decisions 19-20."""
        notes = []
        if ids["work_id"] is not None:
            row = self.con.execute("SELECT book_key FROM books WHERE work_id = ?", (ids["work_id"],)).fetchone()
            if row:
                return row[0], notes
            for col in ("goodreads_book_id", "book_id"):
                if ids[col] is None:
                    continue
                for other_key, other_title in self.con.execute(
                        f"SELECT book_key, title FROM books WHERE {col} = ?", (ids[col],)):
                    notes.append((DECISION, col, str(ids[col]),
                                  f"work_id {ids['work_id']} is new, but {col} {ids[col]} already belongs to "
                                  f'"{other_title}" (book_key {other_key})',
                                  "added as a new book because work_id wins (Rule 9); decide whether they're the same book"))
            return None, notes

        for col in ("goodreads_book_id", "book_id"):
            if ids[col] is not None:
                key = self.one_book(col, ids[col])
                if key is not None:
                    notes.append((CHECK, col, str(ids[col]), f"no work_id, so it was matched on {col}",
                                  "check that it's the same book"))
                    return key, notes
        for col in ("isbn13", "isbn"):
            if cleaned[col]:
                key = self.one_book(col, cleaned[col])
                if key is not None:
                    notes.append((CHECK, col, cleaned[col], f"no work_id or other ID match, so it was matched on {col}",
                                  "check that it's the same book"))
                    return key, notes

        target = (normalize_title(rec["title"]), first_author(rec["authors"]),
                  rec["original_publication_year"].strip())
        possible = [f'"{title}" (book_key {key})' for key, title, authors, year in self.con.execute(
                        "SELECT book_key, title, authors, original_publication_year FROM books ORDER BY book_key")
                    if (normalize_title(title), first_author(authors), (year or "").strip()) == target]
        reason = "no work_id and no matching ID or ISBN; added as a new book"
        if possible:
            reason += f". Possible matches by title, first author and year: {'; '.join(possible)}"
        notes.append((DECISION, "work_id", "", reason, "decide whether it's a book that's already loaded"))
        return None, notes

    def calculated(self, stars, rec, notes):
        result = recalculate(stars)
        if result is None:
            notes.append((CHECK, "ratings_1-ratings_5", ", ".join(str(s) for s in stars),
                          "a star count is missing or isn't a whole number, or there are no ratings, "
                          "so average_rating and work_ratings_count couldn't be recalculated",
                          "kept the file's values"))
            average = as_decimal(rec["average_rating"])
            return {"average_rating": float(average) if average is not None else as_text(rec["average_rating"]),
                    "work_ratings_count": as_stored(rec["work_ratings_count"])}
        total, average = result
        file_average = as_decimal(rec["average_rating"])
        if file_average is None or file_average != average:
            notes.append((CHECK, "average_rating", rec["average_rating"],
                          f"file said {rec['average_rating'] or '(empty)'}, star counts give {average}",
                          "stored the recalculated value"))
        if whole(rec["work_ratings_count"]) != total:
            notes.append((CHECK, "work_ratings_count", rec["work_ratings_count"],
                          f"file said {rec['work_ratings_count'] or '(empty)'}, star counts add up to {total}",
                          "stored the recalculated value"))
        return {"average_rating": float(average), "work_ratings_count": total}

    def insert_book(self, rec, cleaned, ids, notes):
        values = {col: ids[col] if ids[col] is not None else as_text(rec[col]) for col in ID_COLUMNS}
        values.update({col: as_text(rec[col]) for col in DESCRIPTIVE_COLUMNS})
        values.update({col: as_stored(rec[col]) for col in COUNT_COLUMNS})
        values.update(cleaned)
        values.update(self.calculated([rec[c] for c in STAR_COLUMNS], rec, notes))
        values["created_run"] = self.run
        cols = list(values)
        cur = self.con.execute(f"INSERT INTO books ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                               [values[c] for c in cols])
        return cur.lastrowid

    def update_book(self, book_key, line, rec, cleaned, ids, notes):
        """Rule 12 and Decisions 22-25. Returns True if a stored value changed."""
        self.con.row_factory = sqlite3.Row
        stored = dict(self.con.execute("SELECT * FROM books WHERE book_key = ?", (book_key,)).fetchone())
        self.con.row_factory = None
        new = dict(stored)
        reasons = {}

        for col in ID_COLUMNS:
            incoming = ids[col] if ids[col] is not None else as_text(rec[col])
            if incoming is not None and not same(incoming, stored[col]):
                notes.append((CHECK, col, f"stored {stored[col]}, file has {incoming}",
                              f"{col} is different from the stored value, and IDs never change",
                              "kept the stored ID; check that the match is right"))

        for col in DESCRIPTIVE_COLUMNS:
            incoming = as_text(rec[col])
            if incoming is None:
                if stored[col] is not None:
                    self.auto["kept a stored value; the incoming value was empty"] += 1
            elif not same(incoming, stored[col]):
                new[col], reasons[col] = incoming, "different value from a newer file"
                notes.append((CHECK, col, f"{stored[col]!r} -> {incoming!r}", f"{col} changed",
                              "applied the new value; the old one is in the history"))

        for main, cols in ISBN_GROUPS.items():
            if cleaned[main] is None:
                if stored[main] is not None:
                    self.auto["kept a stored ISBN; the incoming value was empty or rejected"] += 1
            elif not same(cleaned[main], stored[main]):
                for col in cols:
                    new[col], reasons[col] = cleaned[col], f"different {main} from a newer file"
                notes.append((CHECK, main, f"{stored[main]} -> {cleaned[main]}",
                              f"a different {main} arrived for a book that's already loaded",
                              "applied the new value; the old one is in the history"))
        new["isbn_row_status"] = row_status(new)
        reasons["isbn_row_status"] = "follows the ISBN statuses"

        for col in COUNT_COLUMNS:
            incoming = as_stored(rec[col])
            if incoming is None:
                if stored[col] is not None:
                    self.auto["kept a stored count; the incoming value was empty"] += 1
            elif not same(incoming, stored[col]):
                new[col], reasons[col] = incoming, "newer count"
                if isinstance(incoming, int) and isinstance(stored[col], int) and incoming < stored[col]:
                    notes.append((CHECK, col, f"{stored[col]} -> {incoming}", f"{col} went down",
                                  "applied the newer value; check that this file really is newer"))

        new.update(self.calculated([new[c] for c in STAR_COLUMNS], rec, notes))
        for col in ("average_rating", "work_ratings_count"):
            reasons[col] = "recalculated from the star counts"

        changed = [col for col in new if col != "book_key" and not same(new[col], stored[col])]
        for col in changed:
            self.con.execute("INSERT INTO history VALUES (?,?,?,?,?,?)",
                             (self.run, book_key, col, None if stored[col] is None else str(stored[col]),
                              None if new[col] is None else str(new[col]), reasons.get(col, "")))
            self.changes.append({"line": line, "book_id": rec["book_id"], "title": rec["title"], "column": col,
                                 "old": stored[col], "new": new[col], "reason": reasons.get(col, "")})
        if changed:
            self.con.execute(f"UPDATE books SET {', '.join(f'{c} = ?' for c in changed)} WHERE book_key = ?",
                             [new[c] for c in changed] + [book_key])
        return bool(changed)

    def check_shared_isbns(self):
        """The same ISBN on different books, in this file or already loaded (Rule 7, Decision 16)."""
        shared = defaultdict(lambda: {"values": set(), "others": set()})
        for col in ("isbn", "isbn13"):
            groups = self.con.execute(f"SELECT {col}, group_concat(book_key) FROM books "
                                      f"WHERE {col} IS NOT NULL GROUP BY {col} HAVING COUNT(*) > 1")
            for value, keys in groups:
                keys = sorted(int(k) for k in keys.split(","))
                if not any(k in self.touched for k in keys):
                    continue
                for k in keys:
                    shared[k]["values"].add(value)
                    shared[k]["others"].update(o for o in keys if o != k)
        for key in sorted(shared):
            others = []
            for other in sorted(shared[key]["others"]):
                title, book_id = self.con.execute("SELECT title, book_id FROM books WHERE book_key = ?",
                                                  (other,)).fetchone()
                others.append(f'"{title}" (book_id {book_id})')
            if key in self.touched:
                line, book_id, title = self.touched[key]
            else:
                line = None
                title, book_id = self.con.execute("SELECT title, book_id FROM books WHERE book_key = ?",
                                                  (key,)).fetchone()
            self.warn(DECISION, line, key, None if book_id is None else str(book_id), title, "isbn",
                      ", ".join(sorted(shared[key]["values"])),
                      f"the same ISBN is also on {', '.join(others)}",
                      "decide which book it really belongs to")


# ---------------------------------------------------------------------------
# A run
# ---------------------------------------------------------------------------

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main(argv):
    if len(argv) != 2:
        print("usage: python ingest.py <file.csv>", file=sys.stderr)
        return 2
    given = argv[1]
    path = Path(given).resolve()

    try:
        log = read_log()
    except RunFailed as e:
        print(f"Failed: {e}. Nothing was changed, and this run couldn't be logged.", file=sys.stderr)
        return 1

    run = 1 + max([e.get("run", 0) for e in log if isinstance(e, dict)] + [0])
    entry = {"run": run, "started_at": now(), "finished_at": None, "status": None, "failure_reason": None,
             "file": {"given": given, "path": str(path), "size_bytes": None, "sha256": None}}
    con = None
    try:
        details, header, records = read_csv(path)
        entry["file"].update(details)
        con = sqlite3.connect(DB_PATH, isolation_level=None)
        con.execute("BEGIN IMMEDIATE")
        for statement in SCHEMA.split(";"):
            if statement.strip():
                con.execute(statement)
        # Never reuse a run number, even if the log was replaced (Decision 28).
        run = max(run, 1 + con.execute("SELECT COALESCE(MAX(run), 0) FROM runs").fetchone()[0])
        entry["run"] = run
        books_before = con.execute("SELECT COUNT(*) FROM books").fetchone()[0]

        job = Load(con, run)
        job.load(header, records)
        counts = {k: job.counts[k] for k in ("rows_read", "loaded", "updated", "skipped_already_exist")}
        counts["rejected"] = len(job.rejected_rows)
        adds_up = counts["rows_read"] == sum(v for k, v in counts.items() if k != "rows_read")
        needs_attention = job.rejected_rows or any(job.warnings.values())

        entry["finished_at"] = now()
        entry["status"] = "loaded_with_warnings" if needs_attention else "loaded"
        con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run, entry["started_at"], entry["finished_at"], entry["status"], given, str(path),
                     details["size_bytes"], details["sha256"], counts["rows_read"], counts["loaded"],
                     counts["updated"], counts["skipped_already_exist"], counts["rejected"]))
        entry.update({
            "counts": counts,
            "adds_up": adds_up,
            "books_before": books_before,
            "books_after": con.execute("SELECT COUNT(*) FROM books").fetchone()[0],
            "isbn_status_counts": {col: {s: job.isbn_statuses[col][s] for s in STATUS_RANK}
                                   for col in ("isbn", "isbn13")},
            "ignored_columns": [c for c in header if c not in EXPECTED_COLUMNS],
            "rejected_rows": job.rejected_rows,
            "needs_decision": job.warnings[DECISION],
            "needs_lookup": job.warnings[LOOKUP],
            "check_this": job.warnings[CHECK],
            "handled_automatically": dict(sorted(job.auto.items())),
            "changes": job.changes,
            "assumptions": ["This file was treated as newer than everything loaded before it (Decision 6)."]
                           + (["Values like 9.78043902348e+12 were treated as rounded to 12 significant "
                               "digits (Decision 10)."] if job.saw_rounded else []),
        })
        con.execute("COMMIT")
    except Exception as e:
        if con is not None and con.in_transaction:
            con.execute("ROLLBACK")
        reason = str(e) if isinstance(e, RunFailed) else f"{type(e).__name__}: {e}"
        entry.update({"finished_at": now(), "status": "failed",
                      "failure_reason": f"{reason}. Nothing was changed; it's safe to run again."})
    finally:
        if con is not None:
            con.close()

    log.append(entry)
    try:
        write_log(log)
    except OSError as e:
        print(f"Warning: the run finished as '{entry['status']}', but {LOG_PATH.name} couldn't be written: {e}",
              file=sys.stderr)
        return 1

    if entry["status"] == "failed":
        print(f"Failed: {entry['failure_reason']}")
        return 1
    c = entry["counts"]
    label = "Loaded with warnings" if entry["status"] == "loaded_with_warnings" else "Loaded"
    print(f"{label}: {given}, {c['rows_read']} rows. {c['loaded']} loaded, {c['updated']} updated, "
          f"{c['skipped_already_exist']} skipped (already exist), {c['rejected']} rejected."
          + ("" if entry["adds_up"] else " THE COUNTS DON'T ADD UP."))
    print(f"Needs a decision: {len(entry['needs_decision'])}, needs a lookup: {len(entry['needs_lookup'])}, "
          f"check this: {len(entry['check_this'])}. Details: {LOG_PATH.name} (run {run}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
