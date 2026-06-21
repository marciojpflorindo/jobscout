#!/usr/bin/env bash
#
# JobScout launcher — double-click this to search for jobs and review them.
#
# It starts the local dashboard, runs the job search against it (the brain scrapes,
# judges, and publishes matches to the dashboard), then opens the dashboard in your
# browser so you can review. The dashboard keeps running until you press Ctrl-C.
#
# First, run install.command once (it builds the sandbox and sets up your profile).
#
# Settings, without searching:
#   ./start.command --setup     re-run the whole interview
#   ./start.command --add-cv    add or replace just your CV
# Extra search options are forwarded to the brain, e.g.:
#   ./start.command --dry-run   judge but don't publish
#   ./start.command --top 10    cap how many postings are judged
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
    --setup|--add-cv)
        if [[ ! -x "$PYBIN" ]]; then
            die "JobScout isn't installed yet." \
                "Double-click install.command first."
        fi
        "$PYBIN" onboarding/interview.py "$1" || true
        exit 0
        ;;
esac

check_arch

# Must be installed first (install.command builds .venv and writes profile.md).
if [[ ! -x "$PYBIN" || ! -f "$PROFILE" ]]; then
    die "JobScout isn't set up yet." \
        "Double-click install.command first — it builds the sandbox and runs the interview." \
        "Then double-click start.command to search for jobs."
fi

check_ollama

# Keep the Mac awake for the whole run (a full search can take several minutes).
# -i prevents idle sleep only; -w "$$" ties caffeinate's lifetime to THIS script,
# so it self-reaps when we exit — that's why the cleanup trap below only needs to
# kill the dashboard, not caffeinate. (If you drop -w "$$", add it to the trap.)
if command -v caffeinate >/dev/null 2>&1; then
    caffeinate -i -w "$$" &
fi

PORT="$(read_port)"
DASH_URL="http://127.0.0.1:${PORT}/"

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

# Search for jobs. The brain publishes good matches to the running dashboard.
header "Searching for jobs (this can take several minutes)…"
"$PYBIN" brain/run.py "$@" || warn "The job search ended with an error (see the messages above)."

# Open the dashboard to review results, then hand the terminal over to it.
say ""
say "Opening the dashboard — review your matches there."
open "$DASH_URL" >/dev/null 2>&1 || true
say ""
say "Dashboard is running at ${DASH_URL}"
say "Press Ctrl-C here to stop it when you're done."
wait "$DASH_PID"
