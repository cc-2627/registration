#!/bin/bash
# Step 6 - the course: name, group size, time zone and classes, in course.json,
# and the README and form students see, generated from it.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"

usage() {
    cat <<'USAGE'
Usage: 06_course.sh [ORG] [REPO] [options] [--yes]

Everything that differs from one course to the next is in course.json, at the
top of the repo. This asks for it (Enter keeps what is there), then writes the
files students see from it:

  .github/README.md                    the page students land on
  .github/ISSUE_TEMPLATE/register.yml  the registration form
  .github/ISSUE_TEMPLATE/config.yml    only the form, no blank issues

and offers to commit and push them, because the bot only runs what is on the
default branch. Until course.json is pushed the workflows do nothing.

Options (any of them skips the questions):
  --name NAME          the course, as students know it
  --min N / --max N    group size, 1 to 6
  --timezone TZ        deadlines are given in this zone, e.g. Europe/Lisbon
  --class SPEC         a weekly session, "NAME DAY END [note]", e.g.
                       "P1 tuesday 16:00 Lab 114"; repeat for each class
  --no-classes         a course without classes: no class field anywhere, and
                       soft deadlines come straight from the assignment
  --number-hint TEXT   what students call their number, e.g. "your *Nº*"
  --yes                ask nothing: keep course.json as it is apart from the
                       flags given, and commit and push without asking
  -h, --help           show this message
USAGE
}

parse_org "${1:-}"
if [ "$ORG_GIVEN" = 1 ]; then shift; fi
REPO=$DEFAULT_REPO
case "${1:-}" in
    -*|"") ;;
    *) REPO=$1; shift ;;
esac
EDIT=()
while [ $# -gt 0 ]; do
    case $1 in
        --name|--min|--max|--timezone|--class|--number-hint)
            [ $# -ge 2 ] || die "$1 needs a value"
            EDIT+=("$1" "$2"); shift ;;
        --no-classes) EDIT+=("$1") ;;
        --yes|-y)     ASSUME_YES=1 ;;
        -h|--help)    usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

command -v python3 >/dev/null 2>&1 || die "python3 not found."
TARGET="$ORG/$REPO"

# --yes means unattended: keep what is in course.json rather than asking.
if [ "$ASSUME_YES" = "1" ]; then EDIT+=(--keep); fi
python3 "$HERE/course.py" edit ${EDIT[@]+"${EDIT[@]}"}
# The course's page, when Pages publishes it, gets a link on the students' page.
# gh prints GitHub's error on stdout, so a repo without Pages has to be caught by the exit code.
PAGE=$(gh api "repos/$TARGET/pages" --jq 'select(.build_type == "workflow") | .html_url' 2>/dev/null) || PAGE=""
python3 "$HERE/course.py" render --repo "$TARGET" ${PAGE:+--page "$PAGE"} | sed "s/^/${GREEN}✓${R} /"

FILES=(course.json .github/README.md .github/ISSUE_TEMPLATE/register.yml .github/ISSUE_TEMPLATE/config.yml)
if [ -z "$(git -C "$ROOT" status --porcelain -- "${FILES[@]}")" ]; then
    ok "Already committed; nothing to change."
    exit 0
fi
git -C "$ROOT" status --short -- "${FILES[@]}" | sed 's/^/    /'
if ! confirm "Commit these and push them to $TARGET?"; then
    say "${DIM}Not committed. The bot keeps using what is on the default branch until you push.${R}"
    exit 0
fi
git -C "$ROOT" add -- "${FILES[@]}"
git -C "$ROOT" commit -q -m "Set up the course: $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$ROOT/course.json")"
ok "committed"
if git -C "$ROOT" remote get-url origin >/dev/null 2>&1; then
    git -C "$ROOT" push -q origin HEAD && ok "pushed"
else
    warn "No origin remote yet - step 2 creates the repo and pushes this."
fi
