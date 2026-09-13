#!/usr/bin/env bash
#
# Cut an immutable, versioned docs snapshot on a new release and move the
# correct moving channel alias onto it.
#
# Channel model (AAASM-2750)
# --------------------------
# The site exposes three *moving* channel aliases plus the frozen, immutable
# per-release versions they point at:
#
#   latest (master)   — tracks master HEAD; re-deployed on every master push by
#                       deploy-latest-version-documentation.sh (never here).
#   pre-release (<tag>) — newest pre-release tag, shown ONLY when it is strictly
#                         greater (semver precedence) than the newest stable.
#   stable (<tag>)      — newest stable tag (e.g. v0.1.0); does NOT exist until
#                         a stable release ships.
#
# This script handles a single release, but it recomputes BOTH moving channels
# from the FULL set of mike versions already on gh-pages (plus the just-released
# tag) — not just the tag it deployed. The channel a tag belongs to is chosen
# from the *real* git release tag shape:
#
#   ^v[0-9]+\.[0-9]+\.[0-9]+$    -> stable
#   ^v[0-9]+\.[0-9]+\.[0-9]+-.+  -> pre-release
#
# Semver gate for the pre-release channel
# ---------------------------------------
# The `pre-release` alias exists ONLY IF the newest pre-release version is
# strictly greater (semver precedence) than the newest stable version; otherwise
# the alias must NOT exist. Precedence: `X.Y.Z-<pre> < X.Y.Z`; among pre-releases
# `v0.1.0-alpha.5 < v0.1.0-alpha.6 < v0.1.0-beta.1 < v0.1.0-beta.2 <
# v0.1.0-rc.1 < v0.1.0`. With no stable, stable precedence is -inf so any
# pre-release shows. So when a stable ships that supersedes the newest
# pre-release, this script REMOVES the `pre-release` alias (the superseded
# pre-release version itself stays as an archived, reachable mike version — only
# the moving alias is dropped) and recomputes stable + the default landing.
# The semver comparison lives in `channel_resolver.py` (unit-tested in
# `test_channel_resolver.py`).
#
# Behaviour:
#   - Reads the real release tag from $RELEASE_TAG (handed in by the docs
#     workflow, which sources it from the release-tag artifact that
#     release-python.yml publishes). The tag — not the PEP-440 pyproject
#     version — is what the version selector label shows, because the tag is
#     the human-facing release identity (recon confirmed `v0.0.1-alpha.5` is a
#     valid mike version id).
#   - Runs `mkdocs build --strict` first as a guard so a broken build never
#     reaches gh-pages.
#   - Calls `mike deploy --push <tag> --title "<tag>"` to freeze the immutable
#     <tag> snapshot (no alias yet — the alias is applied during the recompute
#     so the gate is the single source of truth for which channel exists).
#   - Enumerates every existing version via `mike list`, asks channel_resolver
#     which version each channel should point at, then retargets `stable`,
#     applies or REMOVES `pre-release` per the gate, and sets the site default
#     to the most authoritative channel that exists: stable, else pre-release,
#     else latest.
#
# Required environment:
#   - RELEASE_TAG — the real git release tag (e.g. v0.0.1-alpha.5 or v0.1.0).
#   - GH_TOKEN (or GITHUB_TOKEN) — push access to the gh-pages branch.
#   - Working directory must be the repo root.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"

if [ -z "${RELEASE_TAG:-}" ]; then
    echo "ERROR: RELEASE_TAG is empty — cannot determine the release channel." >&2
    exit 1
fi

# Classify the channel of the just-released tag from its shape. We only use this
# to validate the tag and to log intent; the alias that actually ends up on
# gh-pages is decided by the semver gate during the recompute below.
if [[ "${RELEASE_TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    RELEASED_CHANNEL="stable"
elif [[ "${RELEASE_TAG}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+-.+ ]]; then
    RELEASED_CHANNEL="pre-release"
else
    echo "ERROR: RELEASE_TAG='${RELEASE_TAG}' does not match a stable" \
         "(vX.Y.Z) or pre-release (vX.Y.Z-...) tag shape." >&2
    exit 1
fi

echo "👷  Release tag=${RELEASE_TAG} (released channel=${RELEASED_CHANNEL})"

# Configure git author for the gh-pages commit mike creates.
git config --global user.name "github-actions[bot]"
git config --global user.email "41898282+github-actions[bot]@users.noreply.github.com"

# Make sure the gh-pages branch is locally available — mike reads and pushes
# to it. See https://github.com/jimporter/mike?tab=readme-ov-file#deploying-via-ci
git fetch remote gh-pages --depth=1 2>/dev/null || \
    git fetch origin gh-pages --depth=1 2>/dev/null || true

# Pre-flight: fail fast if the build itself is broken.
mkdocs build --strict

# Freeze the immutable <tag> snapshot. We deploy the bare version id (no channel
# alias yet) with a plain "<tag>" title; the channel alias + final title are set
# during the recompute so the semver gate is the single decision point.
mike deploy --push --title "${RELEASE_TAG}" "${RELEASE_TAG}"

# --- Recompute BOTH channels from the FULL set of mike versions -------------
#
# Enumerate every concrete version id currently on gh-pages (the frozen <tag>
# snapshots), then let channel_resolver apply the semver gate. `mike list`
# prints one line per version, e.g.:
#
#   v0.1.0-rc.1 [pre-release]
#   v0.0.2 [stable, latest]
#   latest (master)
#
# We pull the leading token of each line and keep only real release tags
# (vX.Y.Z / vX.Y.Z-...); the resolver ignores anything else.
RESOLVER="${PROJECT_ROOT}/scripts/ci/channel_resolver.py"
mapfile -t ALL_VERSIONS < <(mike list 2>/dev/null | awk '{print $1}' \
    | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+(-.+)?$' || true)

echo "🔢  Existing versions on gh-pages: ${ALL_VERSIONS[*]:-(none)}"

# Resolve channels. The resolver emits KEY=VALUE lines we eval into the shell.
RESOLVED="$(python3 "${RESOLVER}" resolve "${ALL_VERSIONS[@]}")"
echo "🧮  Resolver output:"
# shellcheck disable=SC2086  # intentional split: one KEY=VALUE per printf line.
printf '      %s\n' ${RESOLVED}
eval "${RESOLVED}"

# Apply the stable channel: retarget it onto the newest stable, if one exists.
if [ -n "${STABLE_VERSION:-}" ]; then
    echo "📌  stable -> ${STABLE_VERSION}"
    mike deploy --push --update-aliases \
        --title "stable (${STABLE_VERSION})" \
        "${STABLE_VERSION}" stable
fi

# Apply the pre-release channel per the semver gate. When the gate says the
# newest pre-release is NOT strictly ahead of stable, the alias must not exist —
# remove it (the frozen pre-release version itself stays reachable). `mike
# delete` on a missing alias is a no-op-with-error, so guard with `mike list`.
if [ "${PRERELEASE_SHOWN:-false}" = "true" ] && [ -n "${PRERELEASE_VERSION:-}" ]; then
    echo "📌  pre-release -> ${PRERELEASE_VERSION} (ahead of stable)"
    mike deploy --push --update-aliases \
        --title "pre-release (${PRERELEASE_VERSION})" \
        "${PRERELEASE_VERSION}" pre-release
elif mike list 2>/dev/null | awk '{print $1}' | grep -qx "pre-release" \
        || mike list 2>/dev/null | grep -qw "pre-release"; then
    echo "🗑️  Removing pre-release alias — newest stable supersedes the newest" \
         "pre-release (the frozen pre-release version stays archived)."
    mike delete --push pre-release
fi

# Set the default (root-redirect target) to the most authoritative channel that
# exists, as decided by the resolver: stable, else pre-release, else latest.
# Use a custom redirect template that carries the GA snippet (advanced Consent
# Mode v2, cookieless-until-consent) so Google's "Test your website" detects the
# tag on the bare-root URL too (AAASM-3558).
echo "🎯  Setting default channel (root redirect) to '${DEFAULT_CHANNEL}'"
mike set-default --push \
    --template "${PROJECT_ROOT}/scripts/ci/templates/mike-redirect-with-analytics.html" \
    "${DEFAULT_CHANNEL}"

# Materialise only the selected moving aliases before adding the scoped UI
# overlay. Concrete release directories and versions.json remain immutable.
bash "${PROJECT_ROOT}/scripts/ci/publish-moving-alias-overlay.sh"

echo "🍻 Release documentation deployed for ${RELEASE_TAG}: stable=${STABLE_VERSION:-(none)}," \
     "pre-release=${PRERELEASE_VERSION:-(hidden)}, default=${DEFAULT_CHANNEL}."
