"""Ollama model tiers + RAM-based recommendation (pure logic, stdlib only).

Tags are PINNED stock Ollama models the author runs and verified at build time
(2026-06-20) — do NOT guess or auto-bump them. Mapping (per the PRD):
  RAM <= 16 GiB  -> qwen 7B-class  (qwen3.5:9b-mlx, ~9 GB on disk)
  RAM  > 16 GiB  -> Gemma MoE      (gemma4:26b-a4b-it-qat, ~15 GB on disk)

These functions are pure so the test suite (Phase 7) can exercise the mapping
without a Mac or a TTY.
"""

from __future__ import annotations

from dataclasses import dataclass

# Author's tested minimum; below this onboarding warns loudly but continues.
MIN_TESTED_RAM_GIB = 16
# Below this the warning is louder still (scoring likely unusably slow).
LOW_RAM_GIB = 8

# Recommend Gemma MoE only when RAM is strictly greater than the tested floor.
GEMMA_RAM_THRESHOLD_GIB = 16


@dataclass(frozen=True)
class Model:
    tag: str
    label: str
    disk_note: str


QWEN = Model(
    tag="qwen3.5:9b-mlx",
    label="Qwen 7B-class (9B, MLX-tuned)",
    disk_note="~9 GB download",
)
GEMMA = Model(
    tag="gemma4:26b-a4b-it-qat",
    label="Gemma MoE (26B sparse, ~4B active)",
    disk_note="~15 GB download",
)


def recommend(ram_gib: int | None) -> Model:
    """Pick the recommended model for the detected RAM.

    Unknown RAM falls back to the smaller, safer Qwen model.
    """
    if ram_gib is not None and ram_gib > GEMMA_RAM_THRESHOLD_GIB:
        return GEMMA
    return QWEN
