#!/usr/bin/env python3
"""Apply an additive UI overlay to Mike's moving docs aliases only.

The current publisher config uses symlink aliases. This tool refuses them:
callers must first materialise only ``stable``/``pre-release`` as physical
copies. That makes a small shell repair possible without mutating a frozen
version tree. ``versions.json`` is read for alias ownership but never written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

MOVING_ALIASES = ("stable", "pre-release")
CSS_NAME = "aaasm-alias-overlay.css"
JS_NAME = "consent-settings-keyboard.js"


def tree_digest(root: Path, ignored: set[str] | None = None) -> str:
    """Return a stable digest of a tree without following symlinks."""
    digest = hashlib.sha256()
    ignored = ignored or set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] in ignored:
            continue
        if path.is_symlink():
            digest.update(f"symlink:{relative}:{os.readlink(path)}".encode())
        elif path.is_file():
            digest.update(f"file:{relative}:".encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def alias_targets(site_root: Path) -> dict[str, str]:
    """Read the selected moving aliases from Mike's immutable manifest."""
    manifest = site_root / "versions.json"
    versions = json.loads(manifest.read_text())
    if not isinstance(versions, list):
        raise ValueError("versions.json must be a list")

    targets: dict[str, str] = {}
    for item in versions:
        if not isinstance(item, dict):
            continue
        version = item.get("version")
        aliases = item.get("aliases")
        if not isinstance(version, str) or not isinstance(aliases, list):
            continue
        for alias in MOVING_ALIASES:
            if alias in aliases:
                if alias in targets:
                    raise ValueError(f"versions.json assigns {alias!r} twice")
                targets[alias] = version
    return targets


def inject_asset(html: str, tag: str, marker: str, closing_tag: str) -> str:
    """Insert one marked asset tag once, retaining all publisher HTML."""
    if marker in html:
        return html
    index = html.lower().find(closing_tag)
    if index < 0:
        raise ValueError(f"missing {closing_tag} in HTML document")
    return html[:index] + tag + "\n" + html[index:]


def overlay_alias(alias_dir: Path, css: Path, javascript: Path) -> int:
    """Copy assets and inject relative references into an alias copy."""
    if alias_dir.is_symlink():
        raise ValueError(f"{alias_dir.name} is a symlink; refusing frozen-tree mutation")
    if not alias_dir.is_dir():
        raise ValueError(f"{alias_dir.name} must be a physical alias directory")

    css_target = alias_dir / "stylesheets" / CSS_NAME
    js_target = alias_dir / "javascripts" / JS_NAME
    css_target.parent.mkdir(parents=True, exist_ok=True)
    js_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(css, css_target)
    shutil.copyfile(javascript, js_target)

    changed = 0
    for html_path in alias_dir.rglob("*.html"):
        html = html_path.read_text()
        # A mike copy can carry source-theme partials (for example under
        # ``_overrides``). They are not standalone published documents and
        # deliberately have no document head/body into which assets belong.
        if "</head>" not in html.lower() or "</body>" not in html.lower():
            continue
        prefix = os.path.relpath(alias_dir, html_path.parent).replace(os.sep, "/")
        asset_prefix = "" if prefix == "." else f"{prefix}/"
        css_marker = f'href="{asset_prefix}stylesheets/{CSS_NAME}"'
        js_marker = f'src="{asset_prefix}javascripts/{JS_NAME}"'
        updated = inject_asset(
            html,
            f'    <link rel="stylesheet" {css_marker}>',
            css_marker,
            "</head>",
        )
        updated = inject_asset(
            updated,
            f"    <script {js_marker}></script>",
            js_marker,
            "</body>",
        )
        if updated != html:
            html_path.write_text(updated)
            changed += 1
    return changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-root", type=Path, required=True)
    parser.add_argument("--css", type=Path, required=True)
    parser.add_argument("--javascript", type=Path, required=True)
    parser.add_argument("--print-targets", action="store_true")
    args = parser.parse_args()

    targets = alias_targets(args.site_root)
    if args.print_targets:
        for alias, version in targets.items():
            print(f"{alias}={version}")
        return

    manifest_before = (args.site_root / "versions.json").read_bytes()
    frozen_before = tree_digest(args.site_root, ignored=set(MOVING_ALIASES))
    changed = sum(overlay_alias(args.site_root / alias, args.css, args.javascript) for alias in targets)
    if (args.site_root / "versions.json").read_bytes() != manifest_before:
        raise RuntimeError("overlay must not modify versions.json")
    if tree_digest(args.site_root, ignored=set(MOVING_ALIASES)) != frozen_before:
        raise RuntimeError("overlay modified a frozen version tree")
    print(f"overlayed aliases: {', '.join(targets) or '(none)'}; HTML files changed: {changed}")


if __name__ == "__main__":
    main()
