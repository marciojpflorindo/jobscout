"""Mac hardware detection for model selection (stdlib only).

Threat model: reads only local machine facts (`platform.machine()` and
`sysctl -n hw.memsize`). `sysctl` is invoked with a fixed argument list and no
shell, so there is no injection surface. On any failure (non-mac, missing
sysctl, unparsable output) detection degrades to "unknown" rather than raising,
so onboarding can warn-and-continue instead of crashing.
"""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Hardware:
    is_apple_silicon: bool
    is_mac: bool
    ram_gib: int | None  # whole GiB (1024^3), or None if undetectable


def _total_ram_gib() -> int | None:
    """Total physical RAM in whole GiB via `sysctl -n hw.memsize`, or None."""
    try:
        out = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        return round(int(out) / (1024 ** 3))
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def detect() -> Hardware:
    is_mac = platform.system() == "Darwin"
    # arm64 => Apple Silicon; x86_64 => Intel. Only meaningful on macOS.
    is_apple_silicon = is_mac and platform.machine() == "arm64"
    return Hardware(
        is_apple_silicon=is_apple_silicon,
        is_mac=is_mac,
        ram_gib=_total_ram_gib() if is_mac else None,
    )
