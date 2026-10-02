#!/bin/bash
# Step 2 - create the public `registration` repo and push this folder to it.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

usage() {
    cat <<'USAGE'
Usage: 02_create_repo.sh [ORG] [REPO] [--yes]

Creates ORG/registration as a PUBLIC repo and pushes this folder to it.
It has to be public: students who aren't org members yet must be able to
open an issue, and a private repo would hide the form from them.

If the repo already exists, nothing is created and you are offered a push
of the current branch instead. Nothing is ever force-pushed.

Options:
  --yes        don't ask for confirmation
  -h, --help   show this message

Needs: gh CLI logged in as an owner of ORG, and this folder committed to git.
USAGE
}

parse_org "${1:-}"
if [ "$ORG_GIVEN" = 1 ]; then shift; fi
REPO=$DEFAULT_REPO
case "${1:-}" in
    -*|"") ;;
    *) REPO=$1; shift ;;
esac
while [ $# -gt 0 ]; do
    case $1 in
        --yes|-y)  ASSUME_YES=1 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

TARGET="$ORG/$REPO"
need_gh
need_org_owner "$ORG"

ROOT=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel 2>/dev/null) \
    || die "This folder is not a git repository. Run: git init && git add -A && git commit -m 'initial commit'"
git -C "$ROOT" rev-parse HEAD >/dev/null 2>&1 \
    || die "No commits yet. Run: git add -A && git commit -m 'initial commit'"
[ -f "$ROOT/.github/workflows/register.yml" ] \
    || die "$ROOT doesn't look like the registration folder (no .github/workflows/register.yml)."

BRANCH=$(git -C "$ROOT" branch --show-current)
[ -n "$BRANCH" ] || die "Detached HEAD; check out a branch first."

if [ -n "$(git -C "$ROOT" status --porcelain)" ]; then
    warn "You have uncommitted changes; they will NOT be pushed."
    git -C "$ROOT" status --short | sed 's/^/    /'
fi

# Cloned from bedel (or any other repo): that remote becomes `upstream`, where
# bedel's fixes come from, and origin is the course's own repo.
cur=$(git -C "$ROOT" remote get-url origin 2>/dev/null || true)
if [ -n "$cur" ] && ! grep -qiE "[:/]$ORG/$REPO(\.git)?/?$" <<<"$cur"; then
    if git -C "$ROOT" remote get-url upstream >/dev/null 2>&1; then
        die "origin points at $cur, not $TARGET, and there is already an upstream remote. Fix the remotes by hand."
    fi
    git -C "$ROOT" remote rename origin upstream
    ok "origin ($cur) is now 'upstream' - pull bedel's updates from it"
fi

if repo_exists "$TARGET"; then
    vis=$(gh repo view "$TARGET" --json visibility --jq .visibility)
    ok "$TARGET already exists ($vis)"
    [ "$vis" = "PUBLIC" ] || warn "It is $vis - students outside the org can't open issues. Change it in Settings."
    if confirm "Push $BRANCH to it?"; then
        git -C "$ROOT" remote get-url origin >/dev/null 2>&1 \
            || git -C "$ROOT" remote add origin "https://github.com/$TARGET.git"
        git -C "$ROOT" push -u origin "$BRANCH"
        ok "pushed $BRANCH"
    else
        say "Not pushed."
    fi
else
    say "${B}Will create${R} https://github.com/$TARGET (public) and push branch '$BRANCH'."
    confirm "Go ahead?" || { say "Skipped."; exit 0; }
    gh repo create "$TARGET" --public --source="$ROOT" --remote=origin --push \
        --description "Group registration, repos and deadlines for $ORG (bedel)"
    ok "created and pushed $TARGET"
fi

say "${DIM}Actions runs workflows from the default branch, so the bot is live once this is pushed.${R}"
