#!/usr/bin/env bash
#
# JobScout installer — double-click this ONCE to set everything up.
#
# It checks your prerequisites, builds a local Python sandbox (.venv), installs
# the pinned dependencies, and walks you through the first-run interview. When it
# finishes you'll have a profile.md + config.json and be ready to search.
#
# After this, use start.command to actually search for jobs and open the dashboard.
#
# Threat model: runs on the user's own Mac, double-clicked. The only inputs are
# the local environment and the interactive interview answers — all trusted. Every
# prerequisite failure exits cleanly with the exact README remedy, not a traceback.

set -euo pipefail

# Run from the repo root regardless of where the file was launched from
# (double-clicking a .command starts in $HOME).
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$SCRIPT_DIR"

# shellcheck source=common.sh
source ./common.sh

rule
say " JobScout — install"
rule

check_arch
find_python      # sets PY (exactly 3.12) or exits with a remedy
check_ollama
ensure_venv

header "Set up your profile"
say "A few quick questions so the model knows what to look for."
if ! "$PYBIN" onboarding/interview.py; then
    die "Onboarding didn't finish, so there's no profile yet." \
        "Run install.command again to retry the interview."
fi

header "Setup complete."
say "Next: double-click start.command to search for jobs and open your dashboard."
say ""
say "Re-run the interview any time:  ./start.command --setup"
say "Add (or replace) a CV later:    ./start.command --add-cv"
