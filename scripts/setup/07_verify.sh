#!/bin/bash
# Step 7 - check the setup and print the link to hand to students. Read-only.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

usage() {
    cat <<'USAGE'
Usage: 07_verify.sh [ORG] [REPO]

Checks everything the earlier steps were supposed to do, changes nothing,
and prints the registration link for students.

Checked: org member privileges, repo exists and is public, both labels,
the ORG_ADMIN_TOKEN and ROSTER secrets, and that the workflows, course.json,
the issue form and the students' README are on the default branch (where Actions runs them from).

Exits non-zero if anything required is missing.
USAGE
}

parse_org "${1:-}"
if [ "$ORG_GIVEN" = 1 ]; then shift; fi
REPO=$(repo_arg "${1:-}")
TARGET="$ORG/$REPO"

need_gh
problems=0
bad() { warn "$*"; problems=$((problems + 1)); }

say "${B}Checking $TARGET${R}"

# --- org privileges (informational: step 1 reports failures itself) ---
if gh api "/orgs/$ORG" --silent >/dev/null 2>&1; then
    [ "$(gh api "/orgs/$ORG" --jq .default_repository_permission)" = "none" ] \
        && ok "base permissions: none" \
        || warn "base permissions are not 'none' (step 1)"
    [ "$(gh api "/orgs/$ORG" --jq .members_can_create_repositories)" = "false" ] \
        && ok "members can't create repositories" \
        || warn "members can still create repositories (step 1)"
else
    warn "can't read /orgs/$ORG settings (owner-only); skipping those checks"
fi

# --- repo ---
if repo_exists "$TARGET"; then
    vis=$(gh repo view "$TARGET" --json visibility --jq .visibility)
    branch=$(gh repo view "$TARGET" --json defaultBranchRef --jq '.defaultBranchRef.name // "?"')
    ok "repo exists (default branch: $branch)"
    [ "$vis" = "PUBLIC" ] \
        && ok "repo is public" \
        || bad "repo is $vis - students who aren't org members can't open issues"
else
    bad "repo $TARGET not found (step 2)"
    branch=""
fi

# --- labels ---
if labels=$(gh label list -R "$TARGET" --limit 200 --json name --jq '.[].name' 2>/dev/null); then
    for l in registration registered; do
        grep -qxF "$l" <<<"$labels" && ok "label $l" || bad "label $l missing (step 3)"
    done
fi

# --- secrets ---
if secrets=$(gh secret list -R "$TARGET" --json name --jq '.[].name' 2>/dev/null); then
    grep -qxF ORG_ADMIN_TOKEN <<<"$secrets" && ok "secret ORG_ADMIN_TOKEN" || bad "secret ORG_ADMIN_TOKEN missing (step 4)"
    grep -qxF ROSTER <<<"$secrets" && ok "secret ROSTER" || bad "secret ROSTER missing (step 5)"
else
    warn "can't list secrets on $TARGET (needs admin on the repo)"
fi

# --- files on the default branch (Actions only ever runs what is there) ---
on_branch() { gh api "/repos/$TARGET/contents/$1?ref=$branch" --silent >/dev/null 2>&1; }

if [ -n "$branch" ]; then
    for f in .github/workflows/register.yml course.json .github/ISSUE_TEMPLATE/register.yml .github/README.md; do
        if on_branch "$f"; then
            ok "$f on $branch"
        else
            bad "$f is not on $branch - step 6 writes it; commit and push it"
        fi
    done

    # The deadline workflow only has work to do once an assignment exists, so
    # before that its absence is a note rather than a problem.
    # gh prints its error body on stdout, so a missing folder needs a numeric check.
    count=$(gh api "/repos/$TARGET/contents/assignments?ref=$branch" \
                --jq '[.[].name | select(endswith(".json"))] | length' 2>/dev/null || true)
    [[ $count =~ ^[0-9]+$ ]] || count=0
    if on_branch .github/workflows/deadlines.yml; then
        ok ".github/workflows/deadlines.yml on $branch"
        [ "$count" -gt 0 ] \
            && ok "$count assignment(s) defined" \
            || skip "no assignments yet - create one with scripts/new_assignment.py"
    elif [ "$count" -gt 0 ]; then
        bad "deadlines.yml is not on $branch but $count assignment(s) exist - nothing will be locked"
    else
        skip "deadlines.yml not on $branch yet - push it before your first assignment"
    fi
fi

say ""
if [ "$problems" -eq 0 ]; then
    ok "${B}Ready.${R}"
else
    warn "$problems problem(s) above."
fi

say ""
say "${B}Link for students:${R}"
say "  https://github.com/$TARGET/issues/new?template=register.yml"
say ""
say "${DIM}Test it first with two accounts of your own: add their numbers to ROSTER,"
say "register a group, have the second account reply /confirm, then delete the"
say "team and repos it created.${R}"

[ "$problems" -eq 0 ]
