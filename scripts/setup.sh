#!/bin/bash
# Run the whole course-edition setup, one step at a time.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$DIR/setup/_common.sh"

usage() {
    cat <<'USAGE'
Usage: setup.sh [ORG] [REPO] [options]

Walks through the setup for a course edition, asking before each step so you
can skip the ones you've already done and re-run the ones you want.

  1  Organization member privileges     setup/01_org_settings.sh
  2  Create the public repo and push    setup/02_create_repo.sh
  3  Create the two labels              setup/03_labels.sh
  4  Admin token (ORG_ADMIN_TOKEN)      setup/04_admin_token.sh
  5  Roster secret from the student CSV setup/05_roster.sh
  6  Course: name, group size, classes setup/06_course.sh
  7  Verify and print the student link  setup/07_verify.sh

Every step is safe to re-run: each checks the current state first and only
changes what differs. Each script also works on its own - see its --help.

Options:
  --only N[,N...]   run just these steps (still asks before each)
  --from N          start at step N
  --roster FILE     student CSV for step 5 (otherwise it asks)
  --test-students N add N fake student numbers in step 5, so you can register
                    a test group with your own accounts
  --yes             run every step without asking (unattended)
  -h, --help        show this message

Answers at each prompt: y = run, n = skip, q = quit.
USAGE
}

parse_org "${1:-}"
if [ "$ORG_GIVEN" = 1 ]; then shift; fi
REPO=$DEFAULT_REPO
case "${1:-}" in
    -*|"") ;;
    *) REPO=$1; shift ;;
esac

ONLY=""; FROM=1; ROSTER_CSV=""; TESTN=""
while [ $# -gt 0 ]; do
    case $1 in
        --only)    ONLY=${2:?--only needs step numbers}; shift ;;
        --from)    FROM=${2:?--from needs a step number}; shift ;;
        --roster)  ROSTER_CSV=${2:?--roster needs a file}; shift ;;
        --test-students) TESTN=${2:?--test-students needs a count}; shift ;;
        --yes|-y)  ASSUME_YES=1 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

TITLES=(
    "Organization member privileges"
    "Create the public repo and push this folder"
    "Create the labels 'registration' and 'registered'"
    "Admin token -> secret ORG_ADMIN_TOKEN"
    "Student roster -> secret ROSTER"
    "The course: name, group size, time zone, classes"
    "Verify everything and print the student link"
)
BLURBS=(
    "Base permissions to none; members can't create repos or teams."
    "Public, so students outside the org can open the form. Nothing is force-pushed."
    "Safe to re-run; labels that exist are left alone."
    "Opens the GitHub page with the right scopes pre-selected, then checks and stores the token."
    "Only student numbers are sent - no names, no status. --test-students adds fake ones."
    "Writes course.json and the README and form students see, then commits and pushes them."
    "Read-only checks, then the link to hand to students."
)
SCRIPTS=(
    "01_org_settings.sh" "02_create_repo.sh" "03_labels.sh" "04_admin_token.sh"
    "05_roster.sh" "06_course.sh" "07_verify.sh"
)

wanted() {
    local n=$1
    [ "$n" -ge "$FROM" ] || return 1
    [ -z "$ONLY" ] && return 0
    grep -qE "(^|,)$n(,|$)" <<<"$ONLY"
}

step_args() {
    case $1 in
        1) printf '%s\0' "$ORG" ;;
        2|3|4|6|7) printf '%s\0%s\0' "$ORG" "$REPO" ;;
        5) printf '%s\0%s\0' "$ORG" "$REPO"
           [ -n "$ROSTER_CSV" ] && printf -- '--csv\0%s\0' "$ROSTER_CSV"
           [ -n "$TESTN" ] && printf -- '--test-students\0%s\0' "$TESTN"
           true ;;
    esac
}

say ""
say "${B}bedel setup${R} - org ${B}$ORG${R}, repo ${B}$ORG/$REPO${R}"
[ "$ASSUME_YES" = "1" ] && say "${DIM}--yes: running every step without asking.${R}"
say ""

declare -a RESULT
for i in "${!TITLES[@]}"; do RESULT[$i]="not run"; done
quit=0

for i in "${!TITLES[@]}"; do
    n=$((i + 1))
    [ "$quit" = "1" ] && break
    wanted "$n" || { RESULT[$i]="skipped (--only/--from)"; continue; }

    script="$DIR/setup/${SCRIPTS[$i]}"
    say "${B}── Step $n: ${TITLES[$i]}${R}"
    say "   ${DIM}${BLURBS[$i]}${R}"

    if [ ! -x "$script" ]; then
        warn "No script for this step yet - do it by hand, see the README."
        RESULT[$i]="do by hand"
        say ""
        continue
    fi

    if [ "$ASSUME_YES" != "1" ]; then
        if [ ! -t 0 ]; then die "Not a terminal; re-run with --yes."; fi
        read -r -p "   Run step $n? [Y/n/q] " ans || true
        case "${ans:-y}" in
            [Qq]*) say "   Stopping here."; RESULT[$i]="not run"; quit=1; continue ;;
            [Nn]*) skip "  skipped"; RESULT[$i]="skipped"; say ""; continue ;;
        esac
    fi
    say ""

    args=(); while IFS= read -r -d '' a; do args+=("$a"); done < <(step_args "$n")
    [ "$ASSUME_YES" = "1" ] && args+=(--yes)

    set +e
    # ${args[@]+...} because bash 3.2 (what macOS ships) treats an empty array
    # as unbound under `set -u`.
    ASSUME_YES=$ASSUME_YES "$script" ${args[@]+"${args[@]}"}
    status=$?
    set -e

    if [ "$status" -eq 0 ]; then
        RESULT[$i]="done"
    else
        if [ "$n" = "7" ]; then
            RESULT[$i]="problems found"
            warn "Step 7 found problems (listed above)."
        else
            RESULT[$i]="FAILED (exit $status)"
            warn "Step $n failed."
        fi
        if [ "$ASSUME_YES" = "1" ]; then
            quit=1
        elif ! confirm "Continue with the remaining steps?" n; then
            quit=1
        fi
    fi
    say ""
done

say "${B}Summary${R}"
failed=0
for i in "${!TITLES[@]}"; do
    case "${RESULT[$i]}" in
        done)    printf '  %s✓%s %d. %s\n' "$GREEN" "$R" "$((i+1))" "${TITLES[$i]}" ;;
        FAILED*|problems*) printf '  %s✗%s %d. %s - %s\n' "$RED" "$R" "$((i+1))" "${TITLES[$i]}" "${RESULT[$i]}"; failed=1 ;;
        *)       printf '  %s·%s %d. %s - %s\n' "$DIM" "$R" "$((i+1))" "${TITLES[$i]}" "${RESULT[$i]}" ;;
    esac
done
say ""
[ "$failed" = "0" ] || die "Some steps need attention. Re-run just those: setup.sh $ORG --only N"
ok "Setup run finished."
