#!/bin/bash
# Step 3 - create the labels the registration workflow needs.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

usage() {
    cat <<'USAGE'
Usage: 03_labels.sh [ORG] [REPO]

Creates the labels the registration bot relies on:
  registration    put on every issue opened from the form
  registered      put on issues of groups that were created (green)
  class-mismatch  put on groups whose class claim differs from the listing

The issue form applies `registration` itself, so the label has to exist
before the first student submits.

Arguments:
  ORG    GitHub organization of the course edition
  REPO   repository name (default: registration)

Options:
  -h, --help   show this message

Labels that already exist are skipped - colours you tweaked by hand are
left alone - so re-running is harmless.
USAGE
}

parse_org "${1:-}"
if [ "$ORG_GIVEN" = 1 ]; then shift; fi
REPO=$(repo_arg "${1:-}")
[ $# -le 2 ] || { usage >&2; exit 2; }
TARGET="$ORG/$REPO"

need_gh
repo_exists "$TARGET" || die "Repository $TARGET not found, or you can't see it."

existing=$(gh label list -R "$TARGET" --limit 200 --json name --jq '.[].name')

create_label() {
    local name=$1
    shift
    if grep -qxF "$name" <<<"$existing"; then
        skip "$name already exists"
    else
        gh label create "$name" -R "$TARGET" "$@" >/dev/null
        ok "created $name"
    fi
}

say "${B}Labels on $TARGET${R}"
create_label registration --description "Group registration request"
create_label registered --color 0E8A16 --description "Team and repos created"
create_label class-mismatch --color D93F0B \
    --description "A member's class differs from the faculty listing - check it"
