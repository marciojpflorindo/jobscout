#!/usr/bin/env python3
"""JobScout first-run interview + model selection (stdlib only).

Threat model:
  Inputs: interactive keyboard answers (the user's own machine — semi-trusted
    but echoed straight back into local files only); an optional CV file PATH
    the user names; the recommended/override model TAG.
  Trust boundary: the model tag is the only value that reaches a subprocess
    (`ollama pull <tag>`). It is run with a fixed argument LIST and no shell, and
    is additionally validated against a strict tag charset, so there is no
    command-injection or argument-injection ("-rf") surface. The CV path is
    resolved, checked to be an existing regular file, and copied into the repo;
    it is never executed.
  Writes: only profile.md, config.json (repo root) and a copied cv.<ext> — all
    gitignored. Never touches git, the dashboard store, or anything outside root.
  Failure: non-interactive stdin (EOFError) and Ctrl-C exit cleanly, never with
    a traceback.

Run:  python3 onboarding/interview.py          (first run / re-run interview)
      python3 onboarding/interview.py --setup   (explicit re-run; same thing,
                                                 confirms before overwriting)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hardware  # noqa: E402
import models  # noqa: E402
import profile_template as pt  # noqa: E402
from profile_template import Answers  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = REPO_ROOT / "profile.md"
CONFIG_PATH = REPO_ROOT / "config.json"

# A safe Ollama tag: letters, digits and . _ : / - only (covers namespaced tags
# like "library/qwen3.5:9b-mlx"). Blocks whitespace, shell metacharacters and
# leading dashes that could be read as flags by `ollama pull`.
VALID_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")

# CV extensions we accept and copy in (stored gitignored at the repo root).
CV_EXTS = (".pdf", ".txt", ".md", ".docx")

# ntfy run-notification defaults. A topic is a public URL path segment; ntfy
# restricts it to this charset, and we validate a pasted one against it.
DEFAULT_NTFY_SERVER = "https://ntfy.sh"
VALID_NTFY_TOPIC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
NTFY_TEST_BODY = "🔔 JobScout test notification — you're all set."
NTFY_TIMEOUT = 10


# --- small input helpers ----------------------------------------------------
class Aborted(Exception):
    """User hit Ctrl-D / Ctrl-C, or stdin closed — bail out cleanly."""


def _bold(text: str) -> str:
    """Bold, but only on a real terminal (no escape codes leak when piped)."""
    return f"\033[1m{text}\033[0m" if sys.stdout.isatty() else text


def section(title: str) -> None:
    """A spaced, bold section header — gives the wall of prompts some structure."""
    print(f"\n{_bold('— ' + title + ' —')}\n")


class Stepper:
    """Hands out '[N of TOTAL] ' tags so the user can see how far along they are.
    Each tag starts with a blank line, so questions don't run together."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.n = 0

    def tag(self) -> str:
        self.n += 1
        return f"\n[{self.n} of {self.total}] "


def _input(prompt: str) -> str:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        raise Aborted from None


def ask(prompt: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    ans = _input(f"{prompt}{hint}\n> ").strip()
    return ans or default


def ask_list(prompt: str) -> list[str]:
    raw = ask(f"{prompt}\n(comma-separated)")
    return [p.strip() for p in raw.split(",") if p.strip()]


def ask_choice(prompt: str, options: tuple[str, ...], default_index: int = 0) -> str:
    print(prompt)
    for i, opt in enumerate(options, 1):
        marker = " (default)" if i - 1 == default_index else ""
        print(f"  {i}. {opt}{marker}")
    while True:
        raw = _input(f"> [{default_index + 1}] ").strip()
        if not raw:
            return options[default_index]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print("  Please enter a number from the list.")


def ask_yes(prompt: str, default_yes: bool = True) -> bool:
    hint = "Y/n" if default_yes else "y/N"
    ans = _input(f"{prompt} [{hint}] ").strip().lower()
    if not ans:
        return default_yes
    return ans in ("y", "yes")


# --- hardware gate + model selection ---------------------------------------
def report_hardware(hw: hardware.Hardware) -> None:
    section("Checking your Mac")
    if not hw.is_mac:
        print("!! JobScout targets macOS. This does not look like a Mac — "
              "you can continue, but nothing here is tested off macOS.")
        return
    if not hw.is_apple_silicon:
        print("!! This Mac looks like Intel, not Apple Silicon.")
        print("   JobScout is UNTESTED on Intel — the local model will likely run")
        print("   CPU-only and be very slow, and may not run at all. You can")
        print("   continue at your own risk.")
    ram = hw.ram_gib
    if ram is None:
        print("   Could not read total RAM; skipping the RAM check.")
    elif ram <= models.LOW_RAM_GIB:
        print(f"!! Only ~{ram} GiB RAM detected — far below the {models.MIN_TESTED_RAM_GIB} GiB")
        print("   tested minimum. Scoring will likely be unusably slow. Continue at your risk.")
    elif ram < models.MIN_TESTED_RAM_GIB:
        print(f"!! ~{ram} GiB RAM detected — below the tested {models.MIN_TESTED_RAM_GIB} GiB")
        print("   minimum. Scoring may be slow or lower quality, but it will run.")
    else:
        print(f"   ~{ram} GiB RAM detected. Good.")


def select_model(hw: hardware.Hardware) -> str:
    rec = models.recommend(hw.ram_gib)
    section("Local model")
    print(f"Recommended for your Mac: {rec.label}")
    print(f"  tag: {rec.tag}  ({rec.disk_note})")
    if ask_yes("Use the recommended model?", default_yes=True):
        tag = rec.tag
    else:
        while True:
            tag = ask("Enter the Ollama model tag to use instead").strip()
            if VALID_TAG.match(tag):
                print("!! Custom model — UNTESTED. We can't vouch for its quality or speed.")
                break
            print("  That doesn't look like a valid Ollama tag; try again.")
    offer_pull(tag)
    return tag


def offer_pull(tag: str) -> None:
    """Offer to `ollama pull <tag>`; if declined, print the command to run."""
    if not VALID_TAG.match(tag):  # defence in depth before any subprocess
        print(f"   (Refusing to pull an unsafe tag: {tag!r})")
        return
    pull_cmd = f"ollama pull {tag}"
    if not ask_yes(f"Download it now with `{pull_cmd}`?", default_yes=True):
        print(f"   OK — run this yourself before the first brain run:\n     {pull_cmd}")
        return
    try:
        print(f"   Running: {pull_cmd}")
        subprocess.run(["ollama", "pull", tag], check=True)
    except FileNotFoundError:
        print("!! `ollama` not found on PATH. Install it (see the README), then run:")
        print(f"     {pull_cmd}")
    except subprocess.CalledProcessError:
        print(f"!! The pull failed. You can retry later:\n     {pull_cmd}")


# --- the interview ----------------------------------------------------------
def run_interview() -> Answers:
    section("Tell JobScout who you are and what you're after")
    print("Answers go only into local files. Nothing is uploaded.")
    print("Each question is one line — separate multiple points with commas.")

    s = Stepper(11)
    a = Answers()
    a.self_description = ask(s.tag() +
        "In one line, how do you describe yourself professionally?\n"
        "(comma-separated, e.g. senior tech writer, 10y in fintech, docs-as-code)")
    a.seniority = ask(s.tag() +
        "What seniority are you targeting? (e.g. mid, senior, lead)")
    a.target_paths = ask_list(s.tag() +
        "List your target roles/paths, best first (e.g. Senior Backend Engineer)")
    a.search_terms = ask_list(s.tag() +
        "Search terms to feed the job boards (e.g. backend engineer, platform engineer)")
    a.country = ask(s.tag() + "Which country are you searching in? (e.g. Germany)")
    a.city = ask(s.tag() + "City (optional — leave blank for country-wide / remote)")
    a.remote_preference = ask_choice(s.tag() +
        "Remote preference:", pt.REMOTE_PREFS, default_index=0)
    a.work_auth = ask(s.tag() +
        "Any work-authorization or location limit? "
        "(e.g. 'EU work rights only', or blank)")
    a.exclude_companies = ask_list(s.tag() + "Companies to exclude entirely (optional)")
    a.avoid_industries = ask_list(s.tag() +
        "Industries to avoid (e.g. gambling, adtech) (optional)")
    a.instant_no = ask(s.tag() +
        "What makes a job an instant no?\n"
        "(comma-separated, e.g. on-site only, no visa sponsorship, adtech)")
    return a


def capture_cv() -> str | None:
    """Ask for an optional CV; copy it into the repo (gitignored). Returns the
    stored relative path, or None if skipped/unusable."""
    section("CV (optional)")
    print("Supply a CV to also get a CV-fit score on each job.")
    print("You can add one later with  ./search-jobs.command --add-cv  — leave blank to skip.")
    print("")
    print("Tip: Markdown, TXT, or DOCX score most reliably; born-digital PDFs work")
    print("too, but a scanned/image-only PDF can't be read and won't be scored.")
    raw = ask("Path to your CV (PDF/TXT/MD/DOCX), or blank to skip")
    if not raw:
        return None
    src = Path(raw).expanduser().resolve()
    if not src.is_file():
        print(f"   No file at {src} — skipping the CV for now.")
        return None
    ext = src.suffix.lower()
    if ext not in CV_EXTS:
        print(f"   Unsupported CV type '{ext}'. Use one of: {', '.join(CV_EXTS)}. Skipping.")
        return None
    dest = REPO_ROOT / f"cv{ext}"
    try:
        shutil.copy(src, dest)
    except OSError as e:
        print(f"   Could not copy the CV ({e}); skipping.")
        return None
    print(f"   Saved CV to {dest.name} (gitignored).")
    return dest.name


def configure_ntfy() -> dict | None:
    """Optional, default-OFF run notifications via ntfy. Returns the config block
    `{enabled, server, topic}` when enabled, or None (nothing written → no
    notifications). Sends one test ping on enable; warns but never blocks if it
    can't reach the server."""
    section("Run notifications (optional)")
    print("JobScout can ping your phone when a run finishes.")
    print("")
    print("It uses ntfy.sh, a free push service: install the ntfy app and")
    print("subscribe to a private 'topic'.")
    print("")
    print("Heads up: ntfy topics are PUBLIC — anyone who knows the topic name")
    print("can read its messages.")
    print("")
    print("JobScout only ever sends a generic 'run finished' message (never a")
    print("job, company, count, or error), and uses a long random topic so it")
    print("stays effectively private. Keep your topic to yourself.")
    print("")
    if not ask_yes("Enable run notifications?", default_yes=False):
        print("   Skipping notifications.")
        return None

    generated = "jobscout-" + secrets.token_urlsafe(24)
    print(f"\n   Generated topic:  {generated}")
    print("")
    print("   Subscribe to EXACTLY this topic in the ntfy app (or open")
    print("   ntfy.sh/<topic> in a browser). It's your secret — long and")
    print("   random so nobody can guess it.")
    print("")
    raw = ask("Press Enter to use this topic, or paste your own", default=generated)
    topic = raw.strip()
    if not VALID_NTFY_TOPIC.match(topic):
        print("   That topic has unsupported characters (use letters, digits, - and _);")
        print(f"   keeping the generated one: {generated}")
        topic = generated

    server = ask("ntfy server base URL (only change this if you self-host)",
                 default=DEFAULT_NTFY_SERVER).strip() or DEFAULT_NTFY_SERVER
    parsed = urlparse(server)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        print(f"   '{server}' isn't a valid http(s) URL — using {DEFAULT_NTFY_SERVER}.")
        server = DEFAULT_NTFY_SERVER
    server = server.rstrip("/")

    _send_test_ping(server, topic)
    return {"enabled": True, "server": server, "topic": topic}


def _send_test_ping(server: str, topic: str) -> None:
    """POST one fixed test message to {server}/{topic}. http/https only, hard
    timeout; on any failure WARN and continue (never block onboarding)."""
    url = f"{server}/{topic}"
    if urlparse(url).scheme not in ("http", "https"):
        print("   (Skipping the test ping — server URL isn't http/https.)")
        return
    try:
        req = urllib.request.Request(
            url, data=NTFY_TEST_BODY.encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"}, method="POST")
        with urllib.request.urlopen(req, timeout=NTFY_TIMEOUT):
            print("   Sent a test notification — check your ntfy app to confirm.")
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"   Couldn't reach ntfy ({e}) — your topic is still saved.")
        print("   Check your network and that you've subscribed to the topic.")


# --- write-out --------------------------------------------------------------
def write_outputs(answers: Answers, model_tag: str, cv_path: str | None,
                  ntfy: dict | None = None) -> None:
    PROFILE_PATH.write_text(pt.render_profile(answers), encoding="utf-8")
    config = pt.build_config(answers, model_tag, cv_path, ntfy)
    CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    section("Done")
    print(f"  Wrote {PROFILE_PATH.name}  (your judging brief — hand-editable)")
    print(f"  Wrote {CONFIG_PATH.name}  (model + search settings the brain reads)")
    print("  Both are gitignored. Re-run `./search-jobs.command --setup` to change them.")


def add_cv_only() -> int:
    """Add or replace just the CV, without redoing the whole interview. Updates
    cv_path in the existing config.json and leaves every other setting (and
    profile.md) untouched."""
    if not CONFIG_PATH.exists():
        print("JobScout isn't set up yet — run install.command first "
              "(or `./search-jobs.command --setup`).")
        return 1
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("config.json is not a JSON object")
    except (OSError, ValueError) as e:
        print(f"Couldn't read config.json ({e}). Re-run full onboarding instead.")
        return 1

    cv_path = capture_cv()
    if cv_path is None:
        print("No CV added — config.json is unchanged.")
        return 0
    config["cv_path"] = cv_path
    CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    print(f"\nUpdated config.json — cv_path = {cv_path}.")
    print("The next job search will add a CV-fit score to each match.")
    return 0


# --- entry point ------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JobScout first-run interview.")
    parser.add_argument("--setup", action="store_true",
                        help="explicitly re-run onboarding (confirms before overwriting)")
    parser.add_argument("--add-cv", action="store_true",
                        help="add or replace just the CV, keeping the rest of your setup")
    args = parser.parse_args(argv)

    print("=" * 60)
    print(" JobScout — onboarding")
    print("=" * 60)

    try:
        if args.add_cv:
            return add_cv_only()
        if PROFILE_PATH.exists():
            who = "--setup re-run" if args.setup else "existing profile found"
            print(f"\nA profile already exists ({who}).")
            if not ask_yes("Overwrite it and redo onboarding?", default_yes=False):
                print("Keeping your existing profile. Nothing changed.")
                return 0

        hw = hardware.detect()
        report_hardware(hw)
        model_tag = select_model(hw)
        answers = run_interview()
        cv_path = capture_cv()
        ntfy = configure_ntfy()
        write_outputs(answers, model_tag, cv_path, ntfy)
        return 0
    except Aborted:
        print("\n\nOnboarding cancelled — nothing was written.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
