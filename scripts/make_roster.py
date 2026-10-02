#!/usr/bin/env python3
"""Extract the student numbers from the student list, one per line.

    python make_roster.py students.csv [--column NAME] | gh secret set ROSTER -R ORG/registration

The list is either a CSV export - the column is found by its header, see
CANDIDATES, or named with --column - or a plain file with one number per line.
Nothing but the numbers is printed, so names and the rest stay on your machine.
"""
import argparse
import csv
import io
import sys

# Headers a student-number column goes by: Portuguese university exports, then
# the usual English ones. Compared without case or surrounding spaces.
CANDIDATES = ("Nº", "N.º", "N°", "No", "Número", "Numero",
              "Number", "Student number", "Student no", "Student ID", "ID")


def read_text(path):
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with open(path, newline="", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    sys.exit(f"Could not decode {path}")


def numbers(text, column=None):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        sys.exit("The list is empty")
    if not column and all(l.isdigit() for l in lines):
        return set(lines)                       # already one number per line
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if not rows:
        sys.exit("The CSV has a header but no rows")
    wanted = [column] if column else CANDIDATES
    wanted = {w.strip().lower() for w in wanted}
    col = next((c for c in rows[0] if c and c.strip().lower() in wanted), None)
    if col is None:
        sys.exit(f"No student-number column found; header is {list(rows[0])}. "
                 "Name it with --column.")
    return {r[col].strip() for r in rows if (r[col] or "").strip().isdigit()}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path")
    ap.add_argument("--column", help="header of the student-number column")
    args = ap.parse_args()
    nums = sorted(numbers(read_text(args.path), args.column), key=lambda s: (len(s), s))
    print("\n".join(nums))
    print(f"{len(nums)} student numbers", file=sys.stderr)


if __name__ == "__main__":
    main()
