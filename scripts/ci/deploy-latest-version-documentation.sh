#!/usr/bin/env bash
#
# Deploy the docs site to the live "latest" version on every push to master.
#
# "latest" tracks master HEAD: it is re-deployed in place on each push and is
# NOT a frozen snapshot of any concrete release. Concrete, immutable version
# snapshots (e.g. 0.1.0) are cut only at release time by
# deploy-stable-version-documentation.sh — never here.
#
# Behaviour:
#   - Runs `mkdocs build --strict` first as a guard so a broken build never
#     reaches gh-pages.
#   - Calls `mike deploy --push --update-aliases latest` to publish/overwrite
#     the "latest" version in place. No concrete version number is minted on
#     master pushes. `--update-aliases` lets mike reclaim "latest" as a concrete
#     version even when older gh-pages state still carries it as an alias of a
#     frozen release (pre-AAASM-2750 the site published "latest" as an alias);
#     without it mike aborts with `version 'latest' already exists`.
#
# Required environment:
#   - GH_TOKEN (or GITHUB_TOKEN) — push access to the gh-pages branch.
#   - Working directory must be the repo root with checked-out master.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"

echo "👷  Deploying docs to the live \"latest\" version (tracks master HEAD)"

# Configure git author for the gh-pages commit mike creates.
git config --global user.name "github-actions[bot]"
git config --global user.email "41898282+github-actions[bot]@users.noreply.github.com"

# Make sure the gh-pages branch is locally available — mike pushes to it.
# See https://github.com/jimporter/mike?tab=readme-ov-file#deploying-via-ci
git fetch remote gh-pages --depth=1 2>/dev/null || \
    git fetch origin gh-pages --depth=1 2>/dev/null || true

# Pre-flight: fail fast if the build itself is broken.
mkdocs build --strict

# Re-deploy the "latest" channel in place. Master pushes never freeze a concrete
# version; frozen, immutable snapshots are cut only at release time by
# deploy-release-version-documentation.sh. The title carries the "(master)"
# suffix so the version selector reads "latest (master)" — making it explicit
# that this channel tracks master HEAD, not a tagged release.
mike deploy --push --update-aliases --title "latest (master)" latest

# Moving release aliases intentionally use physical copies for the narrowly
# scoped readability overlay. This leaves every concrete release snapshot and
# its manifest untouched. The surrounding workflow remains main-only.
bash "${PROJECT_ROOT}/scripts/ci/publish-moving-alias-overlay.sh"

echo "🍻 Latest documentation deployed (live, tracking master)."
