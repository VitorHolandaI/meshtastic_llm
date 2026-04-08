#!/usr/bin/env bash
# Run tests, update coverage badge in README, then commit and push.
# Usage: ./push.sh "commit message"

set -e

VENV="../.venv/bin/activate"
COMMIT_MSG="${1:-chore: update coverage badge}"

# ── activate venv ─────────────────────────────────────────────────────────────
if [ ! -f "$VENV" ]; then
    echo "ERROR: venv not found at $VENV"
    exit 1
fi
source "$VENV"

# ── run tests and capture coverage ───────────────────────────────────────────
echo "Running tests..."
output=$(pytest tests/ --cov=chat_mesh --cov-report=term-missing -q 2>&1)
echo "$output"

# Check tests passed (pytest exits non-zero on failure, set -e handles it)

# ── extract total coverage percentage ────────────────────────────────────────
pct=$(echo "$output" | grep "^TOTAL" | awk '{print $NF}' | tr -d '%')

if [ -z "$pct" ]; then
    echo "ERROR: could not extract coverage percentage"
    exit 1
fi

echo "Coverage: ${pct}%"

# ── pick badge colour ─────────────────────────────────────────────────────────
if   [ "$pct" -ge 90 ]; then colour="brightgreen"
elif [ "$pct" -ge 80 ]; then colour="green"
elif [ "$pct" -ge 70 ]; then colour="yellow"
elif [ "$pct" -ge 60 ]; then colour="orange"
else                          colour="red"
fi

# ── update README badge line ──────────────────────────────────────────────────
sed -i "s|!\[coverage\](https://img.shields.io/badge/coverage-[^)]*)|![coverage](https://img.shields.io/badge/coverage-${pct}%25-${colour})|" README.md

echo "Badge updated to ${pct}% (${colour})"

# ── commit and push ───────────────────────────────────────────────────────────
git add README.md
# Only commit if README actually changed
if ! git diff --cached --quiet; then
    git commit -m "$COMMIT_MSG [coverage: ${pct}%]"
else
    echo "Coverage unchanged, skipping README commit"
fi

git push origin main
git push gitea main

echo "Done."
