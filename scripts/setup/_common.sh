# Shared helpers for the setup step scripts. Sourced, not run.
#
# Every step script works standalone and takes the org as its first argument.
# Set ASSUME_YES=1 to answer every prompt with "yes" (the --yes flag does this).

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    echo "_common.sh is meant to be sourced by the step scripts, not run." >&2
    exit 2
fi

: "${ASSUME_YES:=0}"
: "${DEFAULT_REPO:=registration}"

if [ -t 1 ]; then
    B=$(printf '\033[1m'); DIM=$(printf '\033[2m'); R=$(printf '\033[0m')
    GREEN=$(printf '\033[32m'); YELLOW=$(printf '\033[33m'); RED=$(printf '\033[31m')
else
    B=""; DIM=""; R=""; GREEN=""; YELLOW=""; RED=""
fi

say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$GREEN" "$R" "$*"; }
skip() { printf '%s·%s %s\n' "$DIM" "$R" "$*"; }
warn() { printf '%s!%s %s\n' "$YELLOW" "$R" "$*" >&2; }
die()  { printf '%s✗%s %s\n' "$RED" "$R" "$*" >&2; exit 1; }

# Standard first-argument handling: -h/--help prints usage(), empty prints it to stderr.
# The owner in this checkout's origin URL. Covers git@host:owner/repo.git and
# https://host/owner/repo, with or without the .git suffix.
# Plain BRE in three passes: BSD sed (what macOS ships) has no non-greedy
# repetition, so the suffixes come off before the owner is matched.
# A clone of bedel itself names no course's org, so it gives nothing.
org_from_remote() {
    git remote get-url origin 2>/dev/null \
        | sed -e 's#/*$##' -e 's#\.git$##' -e '\#/bedel$#d' -e 's#.*[:/]\([^/:]*\)/[^/]*$#\1#'
}

# Sets ORG, and ORG_GIVEN=1 when it came from the command line (the caller then
# knows whether to shift it off). With no argument, the organization is taken
# from the checkout you are standing in, so running these from the repo needs
# no org at all.
parse_org() {
    case "${1:-}" in
        -h|--help) usage; exit 0 ;;
        -*)        printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    if [ -n "${1:-}" ]; then
        ORG=$1
        ORG_GIVEN=1
    else
        ORG=$(org_from_remote)
        ORG_GIVEN=0
        if [ -z "$ORG" ]; then
            printf 'No ORG given, and this folder is not a checkout of one.\n' >&2
            usage >&2
            exit 2
        fi
    fi
}

need_gh() {
    command -v gh >/dev/null 2>&1 || die "gh CLI not found. See https://cli.github.com"
    gh auth status >/dev/null 2>&1 || die "Not logged in. Run: gh auth login"
}

need_org_owner() {
    local org=$1 role
    role=$(gh api "/user/memberships/orgs/$org" --jq .role 2>/dev/null) \
        || die "Can't read your membership in '$org'. Does the org exist, and are you a member?"
    [ "$role" = "admin" ] || die "You are '$role' in '$org'; this step needs an org owner."
}

repo_exists() { gh repo view "$1" >/dev/null 2>&1; }

# confirm "question" [default]  -> default is "y" unless given as "n"
confirm() {
    local q=$1 def=${2:-y} ans prompt
    [ "$ASSUME_YES" = "1" ] && return 0
    [ "$def" = "y" ] && prompt="[Y/n]" || prompt="[y/N]"
    if [ ! -t 0 ]; then
        die "Needs an answer to '$q' but stdin is not a terminal. Re-run with --yes."
    fi
    read -r -p "$q $prompt " ans || true
    ans=${ans:-$def}
    case "$ans" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

# Resolve the repo argument shared by most steps.
repo_arg() { printf '%s' "${1:-$DEFAULT_REPO}"; }
