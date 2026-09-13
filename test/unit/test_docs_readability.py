import json
import subprocess
import sys
from pathlib import Path


def test_docs_readability_keeps_native_consent_behavior_out_of_css() -> None:
    """The readability layer may change presentation, never consent semantics."""
    css = Path("docs/stylesheets/aaasm-brand.css").read_text()

    assert ".md-consent__overlay" in css
    assert "backdrop-filter: none" in css
    assert "pointer-events: none" in css
    assert "animation: none" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css
    assert "localStorage" not in css
    assert "__consent" not in css
    assert "gtag" not in css


def test_docs_readability_preserves_existing_consent_and_analytics_contract() -> None:
    """A visual repair must not silently alter the publisher's consent policy."""
    config = Path("mkdocs.yml").read_text()
    analytics = Path(
        "docs/_overrides/partials/integrations/analytics/google.html",
    ).read_text()

    assert "- accept\n      - reject\n      - manage" in config
    assert "checked: true" in config
    assert 'analytics_storage: "denied"' in analytics
    assert 'analytics_storage: "granted"' in analytics


def test_consent_settings_adapter_only_restores_keyboard_access() -> None:
    """Manage settings must be keyboard reachable without owning consent state."""
    config = Path("mkdocs.yml").read_text()
    adapter = Path("docs/javascripts/consent-settings-keyboard.js").read_text()

    assert "javascripts/consent-settings-keyboard.js" in config
    assert 'label[for="__settings"]' in adapter
    assert "control.tabIndex = 0" in adapter
    assert 'event.key !== "Enter" && event.key !== " "' in adapter
    assert "settings.checked = !settings.checked" in adapter
    assert 'new Event("change", { bubbles: true })' in adapter
    assert "event.detail === 0" in adapter
    assert "control.click()" not in adapter
    assert "localStorage" not in adapter
    assert "__md_set" not in adapter
    assert "gtag" not in adapter

    css = Path("docs/stylesheets/aaasm-brand.css").read_text()
    assert 'label[role="button"]:focus-visible' in css


def test_compact_header_reserves_space_for_its_controls() -> None:
    css = Path("docs/stylesheets/aaasm-brand.css").read_text()

    assert ".md-header__inner" in css
    assert "grid-template-columns: auto minmax(0, 1fr) auto auto" not in css
    assert "flex: 1 1 auto" in css
    assert ".md-header__topic:first-child" in css
    assert "text-overflow: ellipsis" in css
    assert "calc(100vw - 9rem)" in css
    assert ".md-version__list" in css
    assert 'content: "Python SDK"' in css
    assert "@media (max-width: 37.5em)" in css
    assert "white-space: normal" in css


def test_alias_overlay_keeps_narrow_archives_readable() -> None:
    css = Path("docs/stylesheets/aaasm-alias-overlay.css").read_text()

    assert 'content: "Python SDK"' in css
    assert "padding-inline: max(0.8rem, 16px)" in css
    assert "overflow-wrap: anywhere" in css
    assert "table { display: block" in css


def test_alias_overlay_only_updates_physical_moving_aliases(tmp_path: Path) -> None:
    """The archive overlay must leave frozen trees and Mike's manifest intact."""
    site = tmp_path / "site"
    frozen = site / "v0.0.1-rc.4"
    frozen.mkdir(parents=True)
    (frozen / "index.html").write_text("<html><head></head><body>frozen</body></html>")
    for alias in ("stable", "pre-release"):
        directory = site / alias
        directory.mkdir()
        (directory / "index.html").write_text(
            "<html><head></head><body>alias</body></html>",
        )
    manifest = [
        {"version": "v0.0.1-rc.4", "title": "rc", "aliases": ["stable", "pre-release"]},
    ]
    (site / "versions.json").write_text(json.dumps(manifest))
    before_manifest = (site / "versions.json").read_bytes()
    before_frozen = (frozen / "index.html").read_bytes()

    subprocess.run(
        [
            sys.executable,
            "scripts/ci/apply_alias_overlay.py",
            "--site-root",
            str(site),
            "--css",
            "docs/stylesheets/aaasm-alias-overlay.css",
            "--javascript",
            "docs/javascripts/consent-settings-keyboard.js",
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            "scripts/ci/apply_alias_overlay.py",
            "--site-root",
            str(site),
            "--css",
            "docs/stylesheets/aaasm-alias-overlay.css",
            "--javascript",
            "docs/javascripts/consent-settings-keyboard.js",
        ],
        check=True,
    )

    assert (site / "versions.json").read_bytes() == before_manifest
    assert (frozen / "index.html").read_bytes() == before_frozen
    for alias in ("stable", "pre-release"):
        html = (site / alias / "index.html").read_text()
        assert html.count("aaasm-alias-overlay.css") == 1
        assert html.count("consent-settings-keyboard.js") == 1


def test_alias_overlay_refuses_a_mike_symlink(tmp_path: Path) -> None:
    """A symlink alias would write through to a frozen snapshot and is rejected."""
    site = tmp_path / "site"
    frozen = site / "v0.0.1-rc.4"
    frozen.mkdir(parents=True)
    (frozen / "index.html").write_text("<html><head></head><body>frozen</body></html>")
    (site / "stable").symlink_to("v0.0.1-rc.4", target_is_directory=True)
    (site / "versions.json").write_text(
        json.dumps([{"version": "v0.0.1-rc.4", "aliases": ["stable"]}]),
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/ci/apply_alias_overlay.py",
            "--site-root",
            str(site),
            "--css",
            "docs/stylesheets/aaasm-alias-overlay.css",
            "--javascript",
            "docs/javascripts/consent-settings-keyboard.js",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "refusing frozen-tree mutation" in result.stderr


def test_alias_overlay_refuses_nested_symlink_or_unsafe_manifest_target(
    tmp_path: Path,
) -> None:
    """Neither a nested link nor a path-like version may escape an alias copy."""
    site = tmp_path / "site"
    alias = site / "stable"
    alias.mkdir(parents=True)
    (alias / "index.html").write_text("<html><head></head><body>alias</body></html>")
    (alias / "linked.html").symlink_to(alias / "index.html")
    (site / "versions.json").write_text(
        json.dumps([{"version": "v0.0.1", "aliases": ["stable"]}]),
    )

    nested_link = subprocess.run(
        [
            sys.executable,
            "scripts/ci/apply_alias_overlay.py",
            "--site-root",
            str(site),
            "--css",
            "docs/stylesheets/aaasm-alias-overlay.css",
            "--javascript",
            "docs/javascripts/consent-settings-keyboard.js",
        ],
        capture_output=True,
        text=True,
    )
    assert nested_link.returncode != 0
    assert "contains symlinked output" in nested_link.stderr

    (alias / "linked.html").unlink()
    (site / "versions.json").write_text(
        json.dumps([{"version": "../frozen", "aliases": ["stable"]}]),
    )
    unsafe_target = subprocess.run(
        [
            sys.executable,
            "scripts/ci/apply_alias_overlay.py",
            "--site-root",
            str(site),
            "--css",
            "docs/stylesheets/aaasm-alias-overlay.css",
            "--javascript",
            "docs/javascripts/consent-settings-keyboard.js",
        ],
        capture_output=True,
        text=True,
    )
    assert unsafe_target.returncode != 0
    assert "unsafe version directory" in unsafe_target.stderr


def test_alias_publisher_materialises_only_moving_aliases_before_publishing() -> None:
    """The release hook keeps Mike metadata and frozen snapshots protected."""
    publisher = Path("scripts/ci/publish-moving-alias-overlay.sh").read_text()

    assert "mike alias --update-aliases --alias-type copy" in publisher
    assert '"${version}" "${alias}"' in publisher
    assert "stable|pre-release" in publisher
    assert 'git show "${PAGES_REMOTE}/${PAGES_BRANCH}:versions.json"' in publisher
    assert "cmp -s" in publisher
    assert 'git rev-parse "${PAGES_BRANCH}:${version}"' in publisher
    assert 'git worktree add --detach "${OVERLAY_WORKTREE}" "${PAGES_BRANCH}"' in publisher
    assert publisher.rindex('push "${PAGES_REMOTE}" "HEAD:${PAGES_BRANCH}"') > publisher.index(
        'git worktree add --detach "${OVERLAY_WORKTREE}" "${PAGES_BRANCH}"',
    )
