"""Put the component dirs on sys.path so the tests can import the modules the
way the app does (the brain/dashboard/onboarding modules use bare imports and
rely on their own directory being importable; `ats` imports as a package from
the repo root). Importing this module performs the setup as a side effect."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

for _sub in ("", "brain", "dashboard", "onboarding"):
    _p = os.path.join(ROOT, _sub) if _sub else ROOT
    if _p not in sys.path:
        sys.path.insert(0, _p)
