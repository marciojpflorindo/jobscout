#!/usr/bin/env bash
#
# Open the JobScout dashboard WITHOUT running a new job search.
#
# Double-click this when you just want to look at and update the jobs you've already
# found — review potential matches, change a status, add notes — with no scraping and
# no waiting. (To search for new jobs, double-click search-jobs.command instead.)
#
# It's a thin wrapper: it just runs search-jobs.command in view-only mode.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$SCRIPT_DIR"

exec ./search-jobs.command --no-search
