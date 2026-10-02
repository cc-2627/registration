#!/bin/bash
# Step 4 - walk through creating the admin PAT and store it as ORG_ADMIN_TOKEN.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

usage() {
    cat <<'USAGE'
Usage: 04_admin_token.sh [ORG] [REPO] [--days N] [--classic] [--token-stdin] [--yes]

GitHub has no API for creating personal access tokens - that is the one
step you have to do in a browser. This script hands you a pre-filled page
for a fine-grained token that reaches ORG and nothing else, checks the
token can do what the bot needs, and stores it as the repo secret
ORG_ADMIN_TOKEN.

The token is read without echoing and never appears in the shell history,
in `ps`, or on disk.

Options:
  --days N        token lifetime in days, 1-366 (default: 180 - about a
                  semester; pick one that ends just after yours does)
  --classic       a classic token instead (admin:org + repo). Account-wide:
                  it reaches every org you own, not just ORG
  --token-stdin   read the token from stdin instead of prompting
  --yes           don't ask for confirmation
  -h, --help      show this message
USAGE
}

parse_org "${1:-}"
if [ "$ORG_GIVEN" = 1 ]; then shift; fi
REPO=$DEFAULT_REPO
case "${1:-}" in
    -*|"") ;;
    *) REPO=$1; shift ;;
esac
FROM_STDIN=0
CLASSIC=0
DAYS=180
while [ $# -gt 0 ]; do
    case $1 in
        --token-stdin) FROM_STDIN=1 ;;
        --classic)     CLASSIC=1 ;;
        --days)        DAYS=${2:-}; shift ;;
        --yes|-y)      ASSUME_YES=1 ;;
        -h|--help)     usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

case "$DAYS" in
    ''|*[!0-9]*) die "--days wants a number of days, 1-366." ;;
esac
[ "$DAYS" -ge 1 ] && [ "$DAYS" -le 366 ] || die "--days must be 1-366 (GitHub's limit)."

TARGET="$ORG/$REPO"
need_gh
repo_exists "$TARGET" || die "Repository $TARGET not found. Run step 2 first."

# What the bot does with the token, in the names GitHub's pre-fill parameters
# use. Checked endpoint by endpoint against GitHub's permission tables:
#   administration  create repos, from templates too; grant teams access to them
#   contents        assignment files, tags, the push archive, class claims
#   issues          announce a released assignment in each group's repo (the
#                   registration issues themselves use the workflow's own token)
#   statuses        mark commits pushed after the soft deadline
#   workflows       starting files or carried-over work that contain workflows
#   members         create teams, add students (which invites them)
PERMISSIONS="administration=write&contents=write&issues=write&statuses=write&workflows=write&members=write"

# Parentheses are percent-encoded: terminals stop auto-linking a URL at an
# unbalanced ")", which would hand you a truncated link. GitHub caps the name
# at 40 characters.
NAME=$(printf 'Registration bot (%s)' "$ORG" | cut -c1-40)
enc() { python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"; }
DESCRIPTION="Creates the course's teams and repos, and locks them at deadlines"
URL="https://github.com/settings/personal-access-tokens/new"
URL+="?name=$(enc "$NAME")&description=$(enc "$DESCRIPTION")"
URL+="&target_name=$ORG&expires_in=$DAYS&$PERMISSIONS"
CLASSIC_URL="https://github.com/settings/tokens/new?scopes=admin:org,repo&description=Registration%20bot%20%28$ORG%29"
if [ "$CLASSIC" = 1 ]; then URL=$CLASSIC_URL; fi

if [ "$FROM_STDIN" = "1" ]; then
    IFS= read -r TOKEN || die "No token on stdin."
else
    if [ "$CLASSIC" = 1 ]; then
        cat <<INSTRUCTIONS

${B}Create the admin token (classic)${R}

  1. Open this page (it pre-selects the scopes for you):

       ${B}$URL${R}

  2. ${B}Expiration${R}  pick a date just after the semester ends - not "No expiration".
  3. ${B}Scopes${R}      ${B}admin:org${R} and ${B}repo${R} are ticked; nothing else is needed.
  4. Click ${B}Generate token${R} and copy the ghp_... value. GitHub shows it once.
  5. If $ORG uses SAML single sign-on, click ${B}Configure SSO${R} next to the new
     token and authorize it for $ORG, or every API call will 403.

  A classic token's scopes are account-wide: this one can administer every
  organization you own, not only $ORG. Drop --classic for one that can't.

INSTRUCTIONS
    else
        cat <<INSTRUCTIONS

${B}Create the admin token${R}

  1. Open this page. It fills in the name, owner ($ORG), a $DAYS-day expiry
     and every permission the bot needs:

       ${B}$URL${R}

  2. ${B}Repository access${R}  choose ${B}All repositories${R}. This is the one field
     GitHub will not take from a link, and the page starts on "Only select".
     It has to be All: the bot creates a repository per group per assignment
     all semester, and "All" is the only setting that covers repos which
     don't exist yet.
  3. Check the ${B}Permissions${R} box lists these (GitHub adds Metadata,
     read-only, by itself):
       Administration, Contents, Issues,
       Commit statuses, Workflows                  Read and write
       Members (organization)                      Read and write
  4. Click ${B}Generate token${R} and copy the github_pat_... value. GitHub shows
     it once; if you lose it, generate a new one.

  The token must belong to an ${B}owner${R} of $ORG. It reaches $ORG and
  nothing else. $ORG has to allow fine-grained tokens (its Settings ->
  Personal access tokens), and may hold yours for an owner's approval first.
  It lasts $DAYS days (--days to change, up to 366) - set a reminder.

INSTRUCTIONS
    fi

    if [ -t 0 ] && [ "$ASSUME_YES" != "1" ]; then
        opener=""
        command -v open >/dev/null 2>&1 && opener=open
        command -v xdg-open >/dev/null 2>&1 && opener=xdg-open
        if [ -n "$opener" ] && confirm "Open that page in your browser now?"; then
            "$opener" "$URL" >/dev/null 2>&1 || warn "Couldn't open a browser; use the link above."
        fi
    fi

    [ -t 0 ] || die "Not a terminal; re-run with --token-stdin."
    printf 'Paste the token (input hidden), or press Enter to skip: '
    IFS= read -rs TOKEN || true
    printf '\n'
fi

[ -n "${TOKEN:-}" ] || { say "No token given; skipping."; exit 0; }
case "$TOKEN" in
    ghp_*|github_pat_*) ;;
    *) warn "That doesn't look like a GitHub token (expected ghp_... or github_pat_...)." ;;
esac

# --- verify before storing ---
headers=$(GH_TOKEN="$TOKEN" gh api -i /user 2>/dev/null) || die "GitHub rejected that token."
login=$(GH_TOKEN="$TOKEN" gh api /user --jq .login)
ok "token belongs to $login"

scopes=$(sed -nE 's/^[Xx]-[Oo][Aa]uth-[Ss]copes:[[:space:]]*(.*)$/\1/p' <<<"$headers" | tr -d '\r')
if [ -z "$scopes" ]; then
    # Fine-grained: no scope list to read, so ask GitHub. Each probe is a write
    # with an empty body. GitHub checks permission before it reads the body, so
    # a token that may do it gets 422 (bad request - nothing is created) and one
    # that may not gets 403.
    probe() {   # probe WHAT METHOD PATH
        local out
        out=$(GH_TOKEN="$TOKEN" gh api -X "$2" "$3" --input - <<<'{}' 2>&1 >/dev/null) || true
        case "$out" in
            *"(HTTP 422)"*) ok "can $1" ;;
            *"(HTTP 403)"*) die "the token cannot $1 - tick the permission listed above and regenerate it" ;;
            *) warn "couldn't tell whether the token can $1: ${out:-no answer}" ;;
        esac
    }
    probe "create teams (Members)"             POST "/orgs/$ORG/teams"
    probe "create repositories (Administration)" POST "/orgs/$ORG/repos"
    probe "write contents (Contents)"           POST "/repos/$TARGET/git/blobs"
    probe "mark commits (Commit statuses)"      POST "/repos/$TARGET/statuses/0000000000000000000000000000000000000000"
    probe "open issues (Issues)"                POST "/repos/$TARGET/issues"

    # "All repositories" can't be read off a token either. What can be seen is
    # whether it reaches every repo you, the owner, can see right now.
    mine=$(gh api --paginate "/orgs/$ORG/repos?type=all&per_page=100" --jq '.[].name' | sort)
    its=$(GH_TOKEN="$TOKEN" gh api --paginate "/orgs/$ORG/repos?type=all&per_page=100" --jq '.[].name' 2>/dev/null | sort)
    missing=$(comm -23 <(printf '%s\n' "$mine") <(printf '%s\n' "$its") | grep -c . || true)
    total=$(printf '%s\n' "$mine" | grep -c . || true)
    if [ "$missing" -gt 0 ]; then
        die "the token reaches only $((total - missing)) of $ORG's $total repositories - regenerate it with Repository access: All repositories"
    fi
    ok "reaches all $total of $ORG's repositories"
    say "${DIM}  (that can't tell 'All' from every repo ticked by hand; only 'All' covers repos made later)${R}"
else
    for need in admin:org repo; do
        grep -qE "(^|, )$need(,|$)" <<<"$scopes" \
            && ok "scope $need" \
            || die "token is missing the '$need' scope (has: $scopes)"
    done
fi

role=$(GH_TOKEN="$TOKEN" gh api "/user/memberships/orgs/$ORG" --jq .role 2>/dev/null || echo "?")
[ "$role" = "admin" ] \
    && ok "$login is an owner of $ORG" \
    || warn "$login is '$role' in $ORG, not an owner - team and repo creation will fail."

confirm "Store this as ORG_ADMIN_TOKEN on $TARGET?" || { say "Not stored."; exit 0; }
printf '%s' "$TOKEN" | gh secret set ORG_ADMIN_TOKEN -R "$TARGET"
ok "ORG_ADMIN_TOKEN set on $TARGET"
say "${DIM}Set a calendar reminder for the expiry date you chose.${R}"
