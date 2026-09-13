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
