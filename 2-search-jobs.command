#!/usr/bin/env bash
#
# JobScout launcher — double-click this to search for jobs and review them.
#
# It starts the local dashboard, runs the job search against it (the brain scrapes,
# judges, and publishes matches to the dashboard), then opens the dashboard in your
# browser so you can review. The dashboard keeps running until you press Ctrl-C.
#
# First, run 1-install.command once (it builds the sandbox and sets up your profile).
#
# Just open the dashboard to look at it, WITHOUT running a new search:
#   ./2-search-jobs.command --no-search   (alias: --view)  — this is what 3-open-dashboard.command runs
# Settings, without searching:
#   ./2-search-jobs.command --setup     re-run the whole interview
#   ./2-search-jobs.command --assist-profile  re-run with optional local-model help
#   ./2-search-jobs.command --add-cv    add or replace just your CV
# Extra search options are forwarded to the brain, e.g.:
#   ./2-search-jobs.command --dry-run   judge but don't publish
#   ./2-search-jobs.command --top 10    cap how many postings are judged
#
# Threat model: runs on the user's own Mac, double-clicked. Inputs are the local
# environment, config.json (user-written), and pass-through flags — all trusted.
# The dashboard binds 127.0.0.1 only; failures exit cleanly with a remedy.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$SCRIPT_DIR"

# shellcheck source=common.sh
source ./common.sh

rule
say " JobScout"
rule

# --- settings-only modes: run onboarding and exit (no search) ---------------
case "${1:-}" in
    --setup|--assist-profile|--add-cv)
        if [[ ! -x "$PYBIN" ]]; then
            die "JobScout isn't installed yet." \
                "Double-click 1-install.command first."
        fi
        "$PYBIN" onboarding/interview.py "$1"
        exit 0
        ;;
esac

# --- view-only mode: open the dashboard without searching -------------------
# Consume the flag so it's never forwarded to the brain (which we skip anyway).
NO_SEARCH=0
case "${1:-}" in
    --no-search|--view)
        NO_SEARCH=1
        shift
        ;;
esac

check_arch

# Must be installed first (1-install.command builds .venv). A search also needs the
# profile; viewing the dashboard does not, so only require it when we'll search.
if [[ ! -x "$PYBIN" ]]; then
    die "JobScout isn't set up yet." \
        "Double-click 1-install.command first — it builds the sandbox and runs the interview."
fi
if (( ! NO_SEARCH )) && [[ ! -f "$PROFILE" ]]; then
    die "JobScout isn't set up yet." \
        "Double-click 1-install.command first — it builds the sandbox and runs the interview." \
        "Then double-click 2-search-jobs.command to search for jobs."
fi

# Ollama and keep-awake are only for the search. Viewing the dashboard needs neither.
if (( ! NO_SEARCH )); then
    check_ollama
    # Keep the Mac awake for the whole run (a full search can take several minutes).
    # -i prevents idle sleep only; -w "$$" ties caffeinate's lifetime to THIS script,
    # so it self-reaps when we exit — that's why the cleanup trap below only needs to
    # kill the dashboard, not caffeinate. (If you drop -w "$$", add it to the trap.)
    if command -v caffeinate >/dev/null 2>&1; then
        caffeinate -i -w "$$" &
    fi
fi

PORT="$(read_port)"
DASH_URL="http://127.0.0.1:${PORT}/"

# Pre-flight: refuse to start if the port is already taken (by any program, on
# either loopback family). Without this, a server squatting IPv6 *:PORT coexists
# with our IPv4 bind — the dashboard looks up, but your browser (and the brain's
# publish) can hit the squatter instead. Caught here with a clear remedy.
if port_in_use "$PORT"; then
    die "Port ${PORT} is already in use by another program." \
        "JobScout's dashboard needs it. Stop that program, or change \"dashboard_port\" in config.json to a free port." \
        "See what's using it:  lsof -nP -iTCP:${PORT} -sTCP:LISTEN"
fi

# Start the dashboard in the background so the brain can publish to it. It stays
# up afterwards for you to review. Always clean it up on exit.
say ""
say "Starting the dashboard on ${DASH_URL} …"
"$PYBIN" dashboard/server.py --port "$PORT" --no-open &
DASH_PID=$!
# caffeinate self-reaps via -w "$$" (see above); only the dashboard needs killing.
cleanup() { kill "$DASH_PID" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

if ! wait_for_dashboard "$PORT" "$DASH_PID"; then
    die "The dashboard didn't come up on port ${PORT}." \
        "Another program may be using that port — close it, or change \"dashboard_port\" in config.json." \
        "See what's using it:  lsof -nP -iTCP:${PORT} -sTCP:LISTEN"
fi
say "Dashboard ready."

# Search for jobs (unless we're in view-only mode). The brain publishes good
# matches to the running dashboard.
if (( ! NO_SEARCH )); then
    header "Searching for jobs (this can take several minutes)…"
    "$PYBIN" brain/run.py "$@" || warn "The job search ended with an error (see the messages above)."
fi

# Open the dashboard, then hand the terminal over to it.
say ""
if (( NO_SEARCH )); then
    say "Opening the dashboard (view only — no new search)."
else
    say "Opening the dashboard — review your matches there."
fi
open "$DASH_URL" >/dev/null 2>&1 || true
say ""
say "Dashboard is running at ${DASH_URL}"
say "Press Ctrl-C here to stop it when you're done."
wait "$DASH_PID"
