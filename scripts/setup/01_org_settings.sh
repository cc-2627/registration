#!/bin/bash
# Step 1 - set the organization member privileges the bot assumes.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

usage() {
    cat <<'USAGE'
Usage: 01_org_settings.sh [ORG] [--dry-run] [--yes]

Sets the org member privileges the registration bot assumes:

  default_repository_permission            none    base permissions: no access
  members_can_create_repositories          false   only the bot creates repos
  members_can_create_public_repositories   false
  members_can_create_private_repositories  false
  members_can_create_pages                 false
  members_can_delete_repositories          false   students can't delete their work
  members_can_change_repo_visibility       false   no accidentally public solutions
  members_can_create_teams                 false   only the bot creates teams

Current values are read first and only the ones that differ are touched, so
re-running is harmless. Each setting is applied on its own, so one your org's
plan doesn't support is reported and the rest still go through.

This does not affect existing repos or teams, only what members may do from
now on. Raising a privilege back is the same command with the opposite value,
or the org UI.

Options:
  --dry-run   show current vs. wanted values, change nothing
  --yes       don't ask for confirmation
  -h, --help  show this message

Needs: gh CLI, logged in as an owner of ORG.
USAGE
}

parse_org "${1:-}"
if [ "$ORG_GIVEN" = 1 ]; then shift; fi
DRY=0
while [ $# -gt 0 ]; do
    case $1 in
        --dry-run) DRY=1 ;;
        --yes|-y)  ASSUME_YES=1 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

SETTINGS=(
    "default_repository_permission=none"
    "members_can_create_repositories=false"
    "members_can_create_public_repositories=false"
    "members_can_create_private_repositories=false"
    "members_can_create_pages=false"
    "members_can_delete_repositories=false"
    "members_can_change_repo_visibility=false"
    "members_can_create_teams=false"
)

need_gh
need_org_owner "$ORG"

say "${B}Organization $ORG${R}"
pending=()
for s in "${SETTINGS[@]}"; do
    key=${s%%=*}; want=${s#*=}
    have=$(gh api "/orgs/$ORG" --jq ".$key // \"unset\"" 2>/dev/null || echo "unset")
    if [ "$have" = "$want" ]; then
        skip "$key already $want"
    elif [ "$have" = "unset" ]; then
        say "  $key: ${DIM}not reported by this org${R} → ${GREEN}$want${R} ${DIM}(will try)${R}"
        pending+=("$s")
    else
        say "  $key: ${YELLOW}$have${R} → ${GREEN}$want${R}"
        pending+=("$s")
    fi
done

if [ ${#pending[@]} -eq 0 ]; then
    ok "Nothing to change."
    exit 0
fi
if [ "$DRY" = "1" ]; then
    say "${DIM}(dry run, nothing changed)${R}"
    exit 0
fi

confirm "Apply ${#pending[@]} change(s) to $ORG?" || { say "Skipped."; exit 0; }

failed=0
for s in "${pending[@]}"; do
    key=${s%%=*}; want=${s#*=}
    case $want in
        true|false) flag=(-F "$key=$want") ;;
        *)          flag=(-f "$key=$want") ;;
    esac
    if gh api -X PATCH "/orgs/$ORG" "${flag[@]}" --silent 2>/dev/null; then
        ok "$key = $want"
    else
        warn "$key could not be set - not supported on this org's plan?"
        failed=1
    fi
done

[ "$failed" = "0" ] || warn "Set the ones above in the org UI: Settings → Member privileges."

# Some orgs accept a setting but don't report it back; say so instead of
# proposing the same change on every run.
unverifiable=()
for s in "${pending[@]}"; do
    key=${s%%=*}; want=${s#*=}
    [ "$(gh api "/orgs/$ORG" --jq ".$key // \"unset\"" 2>/dev/null || echo unset)" = "$want" ] \
        || unverifiable+=("$key")
done
if [ ${#unverifiable[@]} -gt 0 ]; then
    say ""
    warn "These don't read back with the value just set - this org doesn't report them:"
    for k in "${unverifiable[@]}"; do printf '    %s\n' "$k" >&2; done
    warn "Confirm them under Settings → Member privileges. They'll be listed again next run."
fi
