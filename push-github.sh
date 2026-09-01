#!/usr/bin/env bash
# Publish the CURRENT STATE of this repo to the public GitHub mirror — as a
# single commit with no history, force-pushed over whatever is there.
#
# Why not a normal push: the full history (here and on Gitea) contains real
# phone numbers and addresses from early development. Git history cannot be
# unpublished, so the mirror only ever gets the state, never the way there.
# See CLAUDE.md, "Publishing".
#
# Safe to re-run any time; it never touches local branches beyond a throwaway.
set -euo pipefail
cd "$(dirname "$0")"

REMOTE="https://github.com/uneidel/kaim56.git"
BRANCH="pub-$$"                     # throwaway, unique per run

# Refuse to publish a dirty tree: what goes out should be a state you have
# reviewed and committed, not whatever happens to lie around.
if [ -n "$(git status --porcelain)" ]; then
    echo "✗ working tree not clean — commit or stash first:" >&2
    git status --short >&2
    exit 1
fi

# Belt and braces: the patterns that must never appear in a published tree.
# (Cargo.lock checksums false-positive on the phone pattern, hence the filter.)
LEAKS=$(git ls-files -z | xargs -0 grep -lE \
    'sk-or-v1-[A-Za-z0-9]{20}|hf_[A-Za-z0-9]{30}|ghp_[A-Za-z0-9]{30}|github_pat_|AKIA[0-9A-Z]{16}' \
    2>/dev/null | grep -vE 'tests/e2e.py$|push-github.sh$' || true)
if [ -n "$LEAKS" ]; then
    echo "✗ possible secrets in the tree — not publishing:" >&2
    echo "$LEAKS" >&2
    exit 1
fi

cleanup() {
    git checkout -q main 2>/dev/null || true
    git branch -q -D "$BRANCH" 2>/dev/null || true
}
trap cleanup EXIT

git checkout -q --orphan "$BRANCH"
git add -A
git -c user.name=uneidel -c user.email=uneidel@users.noreply.github.com \
    commit -q -m "kAIm56 — self-hosted Firecracker AI-agent platform

One hardened microVM per agent, a single-process manager on the Python
standard library, LLM keys that never enter a VM. Android app, web UI and
Signal as clients; missions, skills, plugins and MCP for the agents.

Published as a single commit: the full history lives in a private Gitea and
contains personal data from early development."
git -c credential.helper=store push --force "$REMOTE" "$BRANCH:main"

echo "✓ published $(git rev-parse --short "$BRANCH") -> $REMOTE (main)"
