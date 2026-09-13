#!/usr/bin/env bash
# Materialise and decorate only Mike's moving release aliases.
#
# Mike normally represents aliases as symlinks.  Decorating a symlink would
# alter the immutable release it targets, so this helper first replaces the
# selected aliases with physical copies and then applies the additive overlay.
# It deliberately never operates on a concrete release directory or versions.json.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${PROJECT_ROOT}"

OVERLAY="${PROJECT_ROOT}/scripts/ci/apply_alias_overlay.py"
CSS="${PROJECT_ROOT}/docs/stylesheets/aaasm-alias-overlay.css"
JAVASCRIPT="${PROJECT_ROOT}/docs/javascripts/consent-settings-keyboard.js"
PAGES_REMOTE="${PAGES_REMOTE:-remote}"
PAGES_BRANCH="${PAGES_BRANCH:-gh-pages}"

for required in "${OVERLAY}" "${CSS}" "${JAVASCRIPT}"; do
    if [ ! -f "${required}" ]; then
        echo "ERROR: required alias-overlay input is missing: ${required}" >&2
        exit 1
    fi
done

git fetch "${PAGES_REMOTE}" "${PAGES_BRANCH}" --depth=1

# Read the published manifest before changing its alias representation. The
# temporary checkout is intentionally separate from the source worktree.
OVERLAY_WORKTREE="$(mktemp -d)"
rmdir "${OVERLAY_WORKTREE}"
cleanup() {
    git worktree remove --force "${OVERLAY_WORKTREE}" 2>/dev/null || true
}
trap cleanup EXIT
git worktree add --detach "${OVERLAY_WORKTREE}" "${PAGES_REMOTE}/${PAGES_BRANCH}"
mapfile -t TARGETS < <(
    python3 "${OVERLAY}" --site-root "${OVERLAY_WORKTREE}" --css "${CSS}" \
        --javascript "${JAVASCRIPT}" --print-targets
)
git worktree remove --force "${OVERLAY_WORKTREE}"

MANIFEST_BEFORE="$(mktemp)"
MANIFEST_AFTER="$(mktemp)"
cleanup() {
    git worktree remove --force "${OVERLAY_WORKTREE}" 2>/dev/null || true
    rm -f "${MANIFEST_BEFORE:-}" "${MANIFEST_AFTER:-}"
}
trap cleanup EXIT
git show "${PAGES_REMOTE}/${PAGES_BRANCH}:versions.json" > "${MANIFEST_BEFORE}"
FROZEN_TREES=()

if [ "${#TARGETS[@]}" -eq 0 ]; then
    echo "No stable/pre-release aliases exist; no overlay publication needed."
    exit 0
fi

for target in "${TARGETS[@]}"; do
    alias="${target%%=*}"
    version="${target#*=}"
    case "${alias}" in
        stable|pre-release) ;;
        *)
            echo "ERROR: refusing non-moving alias '${alias}'" >&2
            exit 1
            ;;
    esac
    FROZEN_TREES+=("${version}=$(git rev-parse "${PAGES_REMOTE}/${PAGES_BRANCH}:${version}")")
    echo "Materialising ${alias} as a copy of ${version}"
    mike alias --update-aliases --alias-type copy --remote "${PAGES_REMOTE}" \
        "${version}" "${alias}"
done

# Mike's alias command is allowed to change the git representation of the two
# aliases, but not their target metadata or any concrete release tree. Validate
# that invariant before the local gh-pages commit is published.
git show "${PAGES_BRANCH}:versions.json" > "${MANIFEST_AFTER}"
if ! cmp -s "${MANIFEST_BEFORE}" "${MANIFEST_AFTER}"; then
    echo "ERROR: alias materialisation changed versions.json" >&2
    exit 1
fi
for frozen in "${FROZEN_TREES[@]}"; do
    version="${frozen%%=*}"
    expected_tree="${frozen#*=}"
    actual_tree="$(git rev-parse "${PAGES_BRANCH}:${version}")"
    if [ "${actual_tree}" != "${expected_tree}" ]; then
        echo "ERROR: alias materialisation changed frozen tree ${version}" >&2
        exit 1
    fi
done
# Work on the validated local gh-pages ref, not the remote branch, so changing
# the alias representation and applying assets become one published update.
git worktree add --detach "${OVERLAY_WORKTREE}" "${PAGES_BRANCH}"

python3 "${OVERLAY}" --site-root "${OVERLAY_WORKTREE}" --css "${CSS}" \
    --javascript "${JAVASCRIPT}"

if git -C "${OVERLAY_WORKTREE}" diff --quiet -- stable pre-release; then
    echo "Moving aliases already contained the current overlay."
    exit 0
fi

git -C "${OVERLAY_WORKTREE}" add -- stable pre-release
git -C "${OVERLAY_WORKTREE}" commit -m "docs: apply moving alias readability overlay"
git -C "${OVERLAY_WORKTREE}" push "${PAGES_REMOTE}" "HEAD:${PAGES_BRANCH}"
