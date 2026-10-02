#!/usr/bin/env python3
"""Extract student number -> class from the class listings, one pair per line.

    python make_classes.py ../classes/* | gh secret set CLASSES -R ORG/registration

Only the number and the class are read. Names never leave the file, and the
secret they go into is what the workflow sees.

The class a file declares on its header line is authoritative, not its filename:
in the listings this was written for, the file named p1 declares "Turno: P2".
Getting that backwards hands two whole classes each other's deadlines, so the
filename is never consulted.
"""
import re
import sys

# The listings are the faculty's own exports and head their column "Turno:",
# which is what this word is in Portuguese. Both spellings are accepted, so a
# translated export works too - but the Portuguese one is the one that arrives.
CLASS_LINE = re.compile(r"\b(?:Turno|Class):\s*([A-Za-z]+\s*\d+)", re.I)
NUMBER = re.compile(r"^(\d{4,6})\b")


def read(path):
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            with open(path, encoding=enc) as f:
                return f.read().splitlines()
        except (UnicodeDecodeError, UnicodeError):
            continue
    sys.exit(f"Could not decode {path}")


def parse(path):
    """(class, [numbers]) for one listing."""
    lines = read(path)
    class_ = None
    for line in lines[:10]:
        m = CLASS_LINE.search(line)
        if m:
            class_ = m.group(1).upper().replace(" ", "")
            break
    if not class_:
        sys.exit(f"{path}: no 'Turno: ...' (or 'Class: ...') header line; "
                 f"refusing to guess from the filename.")

    numbers = []
    for line in lines:
        # The first field of a student row, and only there: a stray year in the
        # title line would otherwise be read as a student.
        first = line.split("\t", 1)[0].strip()
        m = NUMBER.match(first)
        if m and first == m.group(1):
            numbers.append(m.group(1))
    return class_, numbers


def main():
    paths = sys.argv[1:]
    if not paths:
        sys.exit(__doc__)

    seen, by_class = {}, {}
    for path in paths:
        class_, numbers = parse(path)
        if class_ in by_class:
            sys.exit(f"{path}: class {class_} already came from {by_class[class_]}")
        by_class[class_] = path
        for n in numbers:
            if n in seen and seen[n] != class_:
                print(f"warning: {n} is listed in both {seen[n]} and {class_}; "
                      f"keeping {seen[n]}", file=sys.stderr)
                continue
            seen[n] = class_
        print(f"{path}: {class_}, {len(numbers)} students", file=sys.stderr)

    for n in sorted(seen, key=lambda s: (len(s), s)):
        print(f"{n},{seen[n]}")
    print(f"{len(seen)} students across {len(by_class)} classes: "
          f"{', '.join(sorted(by_class))}", file=sys.stderr)


if __name__ == "__main__":
    main()
