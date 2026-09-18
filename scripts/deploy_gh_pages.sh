#!/usr/bin/env bash
# Publish docs/show/html (the PDF show deck rendered to HTML) to the orphan
# gh-pages branch. Run inside WSL from anywhere: bash scripts/deploy_gh_pages.sh
set -euo pipefail

REPO=$(cd "$(dirname "$0")/.." && pwd)
REMOTE=git@github.com:invincible-summer/Next-Tutor-Agent.git
BUILD=${1:-/tmp/ghpages-build}
PAGES_URL=https://invincible-summer.github.io/Next-Tutor-Agent/

cd "$REPO"
SRC_COMMIT=$(git rev-parse --short HEAD)

rm -rf "$BUILD"
mkdir -p "$BUILD"
git archive HEAD docs/show/html | tar -x -C "$BUILD" --strip-components=3
touch "$BUILD/.nojekyll"

cd "$BUILD"
git init -q -b gh-pages
git config user.name "$(git -C "$REPO" config user.name)"
git config user.email "$(git -C "$REPO" config user.email)"
git add -A
git commit -q -m "gh-pages: publish the show-deck HTML (from $SRC_COMMIT docs/show/html)"
git remote add origin "$REMOTE"
git push -f origin gh-pages
echo "published gh-pages from $SRC_COMMIT -> $PAGES_URL"
ls "$BUILD"
