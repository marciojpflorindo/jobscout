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
    it is never executed. Optional setup assistance talks only to local Ollama;
    model output is treated as hostile and validated before it can prefill any
    prompt.
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
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assist  # noqa: E402
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


def ask_list(prompt: str, default: list[str] | None = None) -> list[str]:
    raw = ask(f"{prompt}\n(comma-separated)", default=", ".join(default or []))
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


# --- optional profile/search assistance ------------------------------------
def _show_suggestions(label: str, items: list[assist.Suggestion]) -> None:
    if not items:
        return
    print(f"\n{label}:")
    for i, item in enumerate(items, 1):
        if item.reason:
            print(f"  {i}. {item.text} — {item.reason}")
        else:
            print(f"  {i}. {item.text}")


def _with_progress(message: str, fn):
    """Run a blocking local-model call while printing plain terminal progress."""
    print(f"\n{message}", flush=True)
    done = threading.Event()

    def pulse() -> None:
        elapsed = 0
        if done.wait(5):
            return
        elapsed = 5
        while not done.is_set():
            print(f"  Still working locally ({elapsed}s elapsed)...", flush=True)
            if done.wait(10):
                return
            elapsed += 10

    thread = threading.Thread(target=pulse, daemon=True)
    thread.start()
    start = time.monotonic()
    try:
        return fn()
    finally:
        done.set()
        thread.join(timeout=0.2)
        elapsed = round(time.monotonic() - start)
        print(f"  Local model call finished in {elapsed}s.", flush=True)


def _offer_profile_help(a: Answers, model_tag: str) -> list[str]:
    """Return suggested search terms, or [] when the user should type them from
    scratch. Any accepted target-path refinement mutates `a.target_paths`."""
    section("Optional setup helper")
    print("Job-board search terms are hard to invent, so JobScout can help here.")
    print("")
    print("Target roles/paths describe what you want.")
    print("")
    print("Search terms are different: they are short phrases JobScout sends to")
    print("job boards to collect postings. Too broad means noise; too narrow means")
    print("missed jobs.")
    print("")
    print("If the selected local model is running and downloaded, it can suggest")
    print("clearer roles and search terms from your own answers. You review and edit")
    print("everything before it is written.")
    print("")
    print("If the model is not ready yet, setup still works. JobScout will use your")
    print("own wording and you can rerun this later with ./2-search-jobs.command --setup.")

    fallback = assist.seed_search_terms(a.target_paths)
    ready, reason = assist.ollama_model_ready(model_tag, pt.OLLAMA_BASE)
    if not ready:
        print(f"\nLocal model help is not available yet: {reason}.")
        if fallback:
            print("I can still prefill search terms from the target roles you typed.")
            _show_suggestions("Generic prefill", [assist.Suggestion(t) for t in fallback])
        return fallback

    if not ask_yes("\nUse the local model to suggest clearer roles and search terms?",
                   default_yes=True):
        if fallback:
            print("OK. I will prefill from your own target-role wording instead.")
        return fallback

    result, llm_reason = _with_progress(
        "Asking the local model for setup suggestions. This can take 10-90 seconds.",
        lambda: assist.llm_suggest(a, model_tag, pt.OLLAMA_BASE),
    )
    if result is None:
        print(f"\nThe local model could not produce usable setup suggestions: {llm_reason}.")
        if fallback:
            print("Falling back to your own target-role wording.")
        return fallback

    _show_suggestions("Suggested target roles/paths", result.target_paths)
    _show_suggestions("Suggested job-board search terms", result.search_terms)
    _show_suggestions("Details to consider mentioning later", result.profile_notes)

    if result.target_paths and ask_yes(
        "\nReplace your target roles/paths with the suggested wording?",
        default_yes=False,
    ):
        a.target_paths = [s.text for s in result.target_paths]

    return [s.text for s in result.search_terms] or fallback


def _ask_search_terms(step: Stepper, defaults: list[str]) -> list[str]:
    while True:
        terms = ask_list(step.tag() +
            "Search terms to feed the job boards.\n"
            "Use 3-6 short phrases people would actually type into a job board.\n"
            "These are only for scraping; put nuanced preferences and dealbreakers\n"
            "in the profile questions that follow.", default=defaults)
        cleaned = assist.clean_search_terms(terms, max_items=None)
        notes = assist.search_term_guidance(cleaned)
        if cleaned:
            if cleaned != terms:
                print("\nI cleaned spacing/casing and removed duplicates:")
                print("  " + ", ".join(cleaned))
            if notes:
                print("\nQuick search-term check:")
                for note in notes:
                    print(f"  - {note}")
            if len(cleaned) > assist.MAX_SEARCH_TERMS:
                if ask_yes("Keep all of these anyway?", default_yes=False):
                    return cleaned
                defaults = cleaned[:assist.MAX_SEARCH_TERMS]
                print("Let's tighten the list. Press Enter to accept the shorter default, or edit it.")
                continue
            return cleaned
        print("\nJobScout needs at least one search phrase, or it has nothing to scrape.")


# --- the interview ----------------------------------------------------------
def run_interview(model_tag: str) -> Answers:
    section("Tell JobScout who you are and what you're after")
    print("Answers go only into local files. Nothing is uploaded.")
    print("The first questions teach JobScout what to collect; later questions")
    print("teach the judge what to keep or reject. You can edit the generated")
    print("profile.md by hand or rerun this interview later.")
    print("Each question is one line — separate multiple points with commas.")

    s = Stepper(12)
    a = Answers()
    a.self_description = ask(s.tag() +
        "In one line, how do you describe yourself professionally?\n"
        "Mention the work you do, domains you know, and evidence the judge should care about.\n"
        "(comma-separated, e.g. senior technical writer, API docs, docs-as-code)")
    a.seniority = ask(s.tag() +
        "What seniority are you targeting?\n"
        "This helps the judge down-rank roles that are too junior or too senior.\n"
        "(e.g. mid, senior, lead)")
    a.target_paths = ask_list(s.tag() +
        "List your target roles or paths, best first.\n"
        "These are what you want, not necessarily exact job-board queries.\n"
        "(e.g. Senior Backend Engineer, Developer Advocate, Technical Writer)")
    cleaned_paths = assist.clean_target_paths(a.target_paths, max_items=None)
    if cleaned_paths != a.target_paths:
        print("\nI cleaned spacing/casing and removed duplicate target roles:")
        print("  " + ", ".join(cleaned_paths))
    a.target_paths = cleaned_paths
    suggested_terms = _offer_profile_help(a, model_tag)
    a.search_terms = _ask_search_terms(s, suggested_terms)
    a.country = ask(s.tag() +
        "Which country do you want to find jobs in?\n"
        "This controls the job-board search location; it can be where you'd work,\n"
        "not necessarily where you live now. (e.g. Mexico)")
    a.city = ask(s.tag() +
        "City (optional).\n"
        "Leave blank for country-wide or remote searches.")
    a.extra_countries = ask_list(s.tag() +
        "Search more than one country? Job boards search one country at a time,\n"
        "so list any OTHER countries to search too — or the shortcut 'EU' to\n"
        "cover its member countries (e.g. Canada, United States — or just: EU).\n"
        "Leave blank to search just the country above.")
    a.remote_preference = ask_choice(s.tag() +
        "Remote preference.\n"
        "Remote-only also becomes a hard blocker in the judging profile:",
        pt.REMOTE_PREFS, default_index=0)
    a.work_auth = ask(s.tag() +
        "Any work-authorization limit the judge should enforce on results?\n"
        "This filters what you are shown after scraping. It is separate from WHERE\n"
        "JobScout searches above. (e.g. 'US and Mexico only', or blank)")
    a.exclude_companies = ask_list(s.tag() +
        "Companies to exclude entirely (optional).\n"
        "Use this only for companies you never want to see.")
    a.avoid_industries = ask_list(s.tag() +
        "Industries to avoid (optional).\n"
        "These become strong negative signals or hard blockers for the judge.\n"
        "(e.g. gambling, adtech)")
    a.instant_no = ask(s.tag() +
        "What makes a job an instant no?\n"
        "Use plain language. These become Tier-A hard blockers in profile.md.\n"
        "(comma-separated, e.g. on-site only, no visa sponsorship, adtech)")
    return a


def capture_cv() -> str | None:
    """Ask for an optional CV; copy it into the repo (gitignored). Returns the
    stored relative path, or None if skipped/unusable."""
    section("CV (optional)")
    print("Supply a CV to also get a CV-fit score on each job.")
    print("You can add one later with  ./2-search-jobs.command --add-cv  — leave blank to skip.")
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
    print("  Both are gitignored. Re-run `./2-search-jobs.command --setup` to change them.")


def add_cv_only() -> int:
    """Add or replace just the CV, without redoing the whole interview. Updates
    cv_path in the existing config.json and leaves every other setting (and
    profile.md) untouched."""
    if not CONFIG_PATH.exists():
        print("JobScout isn't set up yet — run 1-install.command first "
              "(or `./2-search-jobs.command --setup`).")
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
    parser.add_argument("--assist-profile", action="store_true",
                        help="re-run onboarding with optional local-model setup help")
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
            if args.assist_profile:
                who = "assisted profile refinement"
            else:
                who = "--setup re-run" if args.setup else "existing profile found"
            print(f"\nA profile already exists ({who}).")
            if not ask_yes("Overwrite it and redo onboarding?", default_yes=False):
                print("Keeping your existing profile. Nothing changed.")
                return 0

        hw = hardware.detect()
        report_hardware(hw)
        model_tag = select_model(hw)
        answers = run_interview(model_tag)
        cv_path = capture_cv()
        ntfy = configure_ntfy()
        write_outputs(answers, model_tag, cv_path, ntfy)
        return 0
    except Aborted:
        print("\n\nOnboarding cancelled — nothing was written.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
