#!/bin/sh
# install-hooks.sh - turn on the local authorship check hooks for this clone.
# Usage: sh tools/install-hooks.sh [--force]
set -u
ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "Run this inside the repository." >&2; exit 1; }
cd "$ROOT" || exit 1

if [ -f .pre-commit-config.yaml ]; then
    cat <<'MSG'
This repo uses the pre-commit framework (.pre-commit-config.yaml), so the
hooks were NOT switched to .githooks. Instead:

  1. Paste the entries from tools/pre-commit-hooks.yaml.snippet into
     .pre-commit-config.yaml (under "repos:").
  2. Run:  pre-commit install --hook-type pre-commit --hook-type commit-msg --hook-type pre-push
MSG
    exit 0
fi

current=$(git config --local --get core.hooksPath || true)
if [ -n "$current" ] && [ "$current" != ".githooks" ] && [ "${1:-}" != "--force" ]; then
    echo "core.hooksPath is already set to \"$current\"; not changing it." >&2
    echo "Re-run with --force to switch to .githooks, or call tools/authorship-check.sh from your existing hooks." >&2
    exit 1
fi

git config core.hooksPath .githooks
chmod +x .githooks/pre-commit .githooks/commit-msg .githooks/pre-push tools/authorship-check.sh 2>/dev/null || true
echo "Authorship check hooks installed (core.hooksPath = .githooks)."
echo "Your commit identity here: $(git config --get user.name) <$(git config --get user.email)>"
echo "Allowed identity: see allow_identity in .authorship-check.conf"
