# Shared helpers for 1-install.command and 2-search-jobs.command.
#
# This file is SOURCED, not executed — the caller has already `cd`'d into the
# repo root and set `set -euo pipefail`. It only defines variables and functions.
#
# Threat model is the same as the launchers: everything here runs on the user's
# own Mac from trusted local inputs (PATH, uname, config.json the user wrote).
# Every subprocess call is fixed-argument / no-shell; nothing is eval'd.

VENV_DIR=".venv"
PROFILE="profile.md"
CONFIG="config.json"
REQ="requirements.txt"
PYBIN="$VENV_DIR/bin/python"
PY=""   # the system Python 3.12, set by find_python (install only)

# --- output helpers ---------------------------------------------------------
# Bold is used only on a real terminal so piped/redirected output stays clean
# (no stray escape codes leaking into a log file).
if [[ -t 1 ]]; then
    _BOLD=$'\033[1m'; _RST=$'\033[0m'
else
    _BOLD=""; _RST=""
fi

say()    { printf '%s\n' "$*"; }
warn()   { printf '!! %s\n' "$*" >&2; }
header() { printf '\n%s%s%s\n\n' "$_BOLD" "$*" "$_RST"; }
rule()   { printf '%s==================================================%s\n' "$_BOLD" "$_RST"; }

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
# Sets the global PY on success; exits with a remedy if none is found.
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

# Ollama must be installed. "Not running" is a warning — the dashboard works
# without it, but the brain (the job search) needs it.
check_ollama() {
    if ! command -v ollama >/dev/null 2>&1; then
        die "Ollama was not found." \
            "Install Homebrew:  https://brew.sh" \
            "Then:  brew install ollama" \
            "Then start it:  open -a Ollama   (or run 'ollama serve' in another tab)"
    fi
    if ! ollama list >/dev/null 2>&1; then
        warn "Ollama is installed but not running — the job search needs it. Start it with:"
        warn "  open -a Ollama   (or run 'ollama serve' in another Terminal tab)"
    fi
}

# --- venv setup (1-install.command) -----------------------------------------

# Create the venv and install pinned deps. Idempotent: skipped once .venv exists.
# Requires PY (call find_python first).
ensure_venv() {
    if [[ -d "$VENV_DIR" ]]; then
        say "Python sandbox (.venv) already present — reusing it."
        return
    fi
    say ""
    say "Setting up a local Python sandbox (.venv)…"
    if ! "$PY" -m venv "$VENV_DIR"; then
        die "Could not create the Python sandbox (.venv)." \
            "Confirm Python 3.12 is healthy:  $PY -m venv --help"
    fi
    say "Installing dependencies (pinned in $REQ)…"
    if ! "$PYBIN" -m pip install --quiet --disable-pip-version-check --upgrade pip; then
        warn "Could not upgrade pip inside the sandbox — continuing with the bundled pip."
    fi
    if ! "$PYBIN" -m pip install --disable-pip-version-check -r "$REQ"; then
        rm -rf "$VENV_DIR"   # leave no half-built sandbox behind, so a retry is clean
        die "Dependency install failed." \
            "Check your internet connection and run 1-install.command again." \
            "(The half-built sandbox was removed so the retry starts clean.)"
    fi
    say "Sandbox ready."
}

# --- dashboard helpers (2-search-jobs.command) ------------------------------------

# Read dashboard_port from config.json via the venv Python (no fragile shell
# JSON parsing). Falls back to 8765 unless the value is a usable TCP port
# (1024-65535), so a typo can't send us binding port 0 or an out-of-range value.
read_port() {
    local p
    p="$("$PYBIN" - <<'PY' 2>/dev/null || true
import json
try:
    print(int(json.load(open("config.json")).get("dashboard_port", 8765)))
except Exception:
    print(8765)
PY
)"
    if ! [[ "$p" =~ ^[0-9]+$ ]] || (( p < 1024 || p > 65535 )); then
        p=8765
    fi
    printf '%s' "$p"
}

# Is something already listening on port $1, on EITHER loopback family? Returns 0
# (in use) / 1 (free). Run BEFORE starting our own dashboard. This catches a
# squatter that wait_for_dashboard cannot: a server bound to IPv6 *:PORT coexists
# with our IPv4 127.0.0.1:PORT bind, so our dashboard comes up "fine" yet the
# browser can still land on the squatter. Checking both 127.0.0.1 and ::1 sees it.
port_in_use() {
    "$PYBIN" - "$1" <<'PY' 2>/dev/null
import socket, sys
port = int(sys.argv[1])
def busy(family, addr):
    try:
        s = socket.socket(family, socket.SOCK_STREAM)
    except OSError:
        return False
    s.settimeout(0.5)
    try:
        s.connect((addr, port)); return True
    except OSError:
        return False
    finally:
        s.close()
sys.exit(0 if busy(socket.AF_INET, "127.0.0.1") or busy(socket.AF_INET6, "::1") else 1)
PY
}

# Poll until the JobScout dashboard answers on $1 with a valid /api/data payload.
# Returns 0 once it's up, 1 after ~15s. This is primarily a liveness probe for the
# server we just spawned; the payload-shape check also means a non-JobScout server
# that happens to hold the port (e.g. a plain http.server) is NOT mistaken for ours.
# If $2 (the server PID) is given, it bails out immediately when that process dies
# — e.g. a failed bind because the port was already taken — instead of waiting out
# the full timeout.
wait_for_dashboard() {
    local port="$1" pid="${2:-}" i
    for ((i = 0; i < 30; i++)); do
        if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
            return 1   # the dashboard process exited (most likely couldn't bind)
        fi
        if "$PYBIN" - "$port" <<'PY' >/dev/null 2>&1
import json, sys, urllib.request
port = sys.argv[1]
with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/data", timeout=2) as r:
    data = json.load(r)
sys.exit(0 if isinstance(data, dict) and "rows" in data else 1)
PY
        then
            return 0
        fi
        sleep 0.5
    done
    return 1
}
