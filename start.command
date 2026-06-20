#!/usr/bin/env bash
#
# JobScout launcher — double-click this file (or run it in Terminal).
#
# First run:  checks prerequisites, creates a local Python sandbox (.venv),
#             installs the pinned dependencies, runs the onboarding interview,
#             then opens the dashboard.
# Later runs: just opens the dashboard.
# Re-run onboarding any time:  ./start.command --setup
#
# Threat model: this runs on the user's own Mac, double-clicked. The only inputs
# are the local environment (PATH, `uname`, whether python3.12 / ollama exist)
# and an optional pass-through flag (--setup) — all trusted. Every prerequisite
# failure exits cleanly with the exact README remedy, never a Python traceback.

set -euo pipefail

# Run from the repo root regardless of where the file was launched from
# (double-clicking a .command starts in $HOME).
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$SCRIPT_DIR"

VENV_DIR=".venv"
PROFILE="profile.md"
REQ="requirements.txt"

# --- tiny output helpers ----------------------------------------------------
say()  { printf '%s\n' "$*"; }
warn() { printf '!! %s\n' "$*" >&2; }

# Print a remedy and exit cleanly (no traceback). $1 = headline, rest = steps.
die() {
    local head="$1"; shift
    printf '\n!! %s\n' "$head" >&2
    local step
    for step in "$@"; do
        printf '   %s\n' "$step" >&2
    done
    printf '\nSee the README ("Install") for the full walkthrough.\n' >&2
    exit 1
}

# --- prerequisite checks ----------------------------------------------------

# Apple Silicon: a warning, not a blocker (Intel is untested, not forbidden).
check_arch() {
    if [[ "$(uname -s)" != "Darwin" ]]; then
        warn "This isn't macOS. JobScout is built and tested only on macOS — continuing anyway."
        return
    fi
    if [[ "$(uname -m)" != "arm64" ]]; then
        warn "This Mac looks like Intel, not Apple Silicon. The local model will likely"
        warn "run CPU-only and be very slow. Continuing at your own risk."
    fi
}

# Find a Python that is *exactly* 3.12 (the version the deps are pinned for).
# Echoes the interpreter name on success; exits with a remedy if none found.
PY=""
find_python() {
    local cand ver
    for cand in python3.12 python3; do
        if command -v "$cand" >/dev/null 2>&1; then
            ver="$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
            if [[ "$ver" == "3.12" ]]; then
                PY="$cand"
                return 0
            fi
        fi
    done
    die "Python 3.12 was not found." \
        "Install Homebrew:  https://brew.sh" \
        "Then:  brew install python@3.12"
}

# Ollama must be installed (the brain and onboarding need it). "Not running" is
# only a warning here — the dashboard works without it; the brain needs it.
check_ollama() {
    if ! command -v ollama >/dev/null 2>&1; then
        die "Ollama was not found." \
            "Install Homebrew:  https://brew.sh" \
            "Then:  brew install ollama" \
            "Then start it:  open -a Ollama   (or run 'ollama serve' in another tab)"
    fi
    if ! ollama list >/dev/null 2>&1; then
        warn "Ollama is installed but not running. Start it before a brain run:"
        warn "  open -a Ollama   (or run 'ollama serve' in another Terminal tab)"
    fi
}

# --- first-run setup --------------------------------------------------------

# Create the venv and install pinned deps. Idempotent: skipped once .venv exists.
ensure_venv() {
    if [[ -d "$VENV_DIR" ]]; then
        return
    fi
    say ""
    say "First run — setting up a local Python sandbox (.venv)…"
    if ! "$PY" -m venv "$VENV_DIR"; then
        die "Could not create the Python sandbox (.venv)." \
            "Confirm Python 3.12 is healthy:  $PY -m venv --help"
    fi
    say "Installing dependencies (pinned in $REQ)…"
    if ! "$VENV_DIR/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip; then
        warn "Could not upgrade pip inside the sandbox — continuing with the bundled pip."
    fi
    if ! "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r "$REQ"; then
        rm -rf "$VENV_DIR"   # leave no half-built sandbox behind, so a retry is clean
        die "Dependency install failed." \
            "Check your internet connection and retry by double-clicking start.command again." \
            "(The half-built sandbox was removed so the retry starts clean.)"
    fi
    say "Sandbox ready."
}

# --- main -------------------------------------------------------------------

say "=================================================="
say " JobScout"
say "=================================================="

check_arch
find_python
check_ollama
ensure_venv

PYBIN="$VENV_DIR/bin/python"

# --setup: re-run onboarding explicitly, then open the dashboard.
if [[ "${1:-}" == "--setup" ]]; then
    "$PYBIN" onboarding/interview.py --setup || true
elif [[ ! -f "$PROFILE" ]]; then
    # First run (no profile yet): walk through onboarding before launching.
    say ""
    say "No profile yet — let's set one up."
    if ! "$PYBIN" onboarding/interview.py; then
        die "Onboarding didn't finish, so there's no profile to run against yet." \
            "Re-run it any time:  ./start.command --setup"
    fi
fi

say ""
say "Opening the dashboard… (press Ctrl-C here to stop it)"
exec "$PYBIN" dashboard/server.py
