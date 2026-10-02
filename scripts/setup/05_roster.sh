#!/bin/bash
# Step 5 - turn the student CSV into the ROSTER secret.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

usage() {
    cat <<'USAGE'
Usage: 05_roster.sh [ORG] [REPO] [--csv students.csv] [--test-students N] [--yes]

Also sets CLASSES from the class listings next to the CSV (../classes or
../turnos by default, or CLASSES_DIR=...), so deadlines follow each class's slot.

Extracts the student-number column from the CSV with make_roster.py and
stores it as the repo secret ROSTER. Only the numbers leave your machine -
no names, no status. Re-run this whenever enrolment changes.

The numbers are held in a shell variable and piped straight to gh, so no
temporary file with student data is written.

Test students: --test-students N adds N fake numbers (99901, 99902, ...) to
the roster so you can register a group with your own GitHub accounts without
touching a real student's number. They are printed so you can paste them into
the form. Re-run without the flag to drop them again before the course starts.

Options:
  --csv FILE          the exported student list (asked for if omitted)
  --test-students N   also allow N fake numbers, 99901 upwards
  --yes               don't ask for confirmation
  -h, --help          show this message
USAGE
}

parse_org "${1:-}"
if [ "$ORG_GIVEN" = 1 ]; then shift; fi
REPO=$DEFAULT_REPO
case "${1:-}" in
    -*|"") ;;
    *) REPO=$1; shift ;;
esac
CSV=""; TESTN=0
while [ $# -gt 0 ]; do
    case $1 in
        --csv)            CSV=${2:?--csv needs a file}; shift ;;
        --test-students)  TESTN=${2:?--test-students needs a count}; shift ;;
        --yes|-y)         ASSUME_YES=1 ;;
        -h|--help)        usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done
[[ $TESTN =~ ^[0-9]+$ ]] || die "--test-students needs a number, got '$TESTN'"
[ "$TESTN" -le 90 ] || die "--test-students is capped at 90."

TARGET="$ORG/$REPO"
need_gh
command -v python3 >/dev/null 2>&1 || die "python3 not found."
repo_exists "$TARGET" || die "Repository $TARGET not found. Run step 2 first."

if [ -z "$CSV" ]; then
    [ -t 0 ] || die "No --csv given and stdin is not a terminal."
    read -r -p "Path to the student CSV (empty to skip): " CSV || true
    [ -n "$CSV" ] || { say "Skipped."; exit 0; }
fi
CSV=${CSV/#\~/$HOME}
[ -f "$CSV" ] || die "File not found: $CSV"

MK="$(dirname "${BASH_SOURCE[0]}")/../make_roster.py"
roster=$(python3 "$MK" "$CSV") || die "make_roster.py could not read $CSV"
count=$(grep -c . <<<"$roster" || true)
[ "${count:-0}" -gt 0 ] || die "No student numbers found in $CSV"

ok "$count student numbers found"

if [ "$TESTN" -gt 0 ]; then
    tests=""
    for i in $(seq 1 "$TESTN"); do tests+=$(printf '999%02d\n' "$i")$'\n'; done
    roster="$roster"$'\n'"${tests%$'\n'}"
    count=$((count + TESTN))
    ok "plus $TESTN test student(s): $(tr '\n' ' ' <<<"${tests%$'\n'}")"
    warn "Remove these before the course starts: re-run without --test-students."
fi

say "${DIM}(real numbers are not printed; make_roster.py reported the count above)${R}"
confirm "Store $count number(s) as the ROSTER secret on $TARGET?" || { say "Not stored."; exit 0; }

printf '%s\n' "$roster" | gh secret set ROSTER -R "$TARGET"
ok "ROSTER set on $TARGET ($count numbers)"

# --- classes: which practical class each student is in ---
# classes/, or turnos/ - the name the faculty's own export folder tends to have.
CLASSES_DIR=${CLASSES_DIR:-$(dirname "$CSV")/classes}
if [ ! -d "$CLASSES_DIR" ] && [ -d "$(dirname "$CSV")/turnos" ]; then
    CLASSES_DIR="$(dirname "$CSV")/turnos"
fi
if [ -d "$CLASSES_DIR" ]; then
    MKT="$(dirname "${BASH_SOURCE[0]}")/../make_classes.py"
    if classes=$(python3 "$MKT" "$CLASSES_DIR"/* 2>&1 >/tmp/classes.$$); then
        say "$classes"
        tcount=$(grep -c . /tmp/classes.$$ || true)
        if confirm "Store $tcount student/class pair(s) as the CLASSES secret?"; then
            gh secret set CLASSES -R "$TARGET" < /tmp/classes.$$
            ok "CLASSES set on $TARGET ($tcount pairs)"
        else
            say "Not stored."
        fi
    else
        warn "Could not read the class listings: $classes"
    fi
    rm -f /tmp/classes.$$
else
    skip "no class listings at $CLASSES_DIR - per-class deadlines will not apply"
    say "${DIM}Set CLASSES_DIR=... if they live elsewhere.${R}"
fi
