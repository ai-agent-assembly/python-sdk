/*
 * Material's consent "Manage settings" control is a label for the native
 * __settings checkbox, but its runtime assigns it tabindex="-1". Restore the
 * ordinary keyboard contract for that existing control without replacing the
 * consent form or changing its submit/reset/storage behavior.
 */
(function () {
  function enableConsentSettingsKeyboard() {
    var control = document.querySelector(
      '[data-md-component="consent"] label[for="__settings"]',
    );
    var settings = document.getElementById("__settings");

    if (!control || !settings) {
      return;
    }

    control.tabIndex = 0;
    control.setAttribute("role", "button");
    control.setAttribute("aria-controls", "__settings");
    control.setAttribute("aria-expanded", String(settings.checked));

    control.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") {
        return;
      }
      event.preventDefault();
      // This hidden Material checkbox doesn't receive synthetic activation
      // through its label. Toggle only its transient disclosure state, then
      // let the native bubbling change event update Material's UI.
      settings.checked = !settings.checked;
      settings.dispatchEvent(new Event("change", { bubbles: true }));
      control.setAttribute("aria-expanded", String(settings.checked));
    });

    settings.addEventListener("change", function () {
      control.setAttribute("aria-expanded", String(settings.checked));
    });

    // Enter on a label synthesizes a click after keydown in some browsers.
    // The key handler has already toggled the native checkbox, so suppress
    // only that keyboard-generated label default action. Pointer label clicks
    // retain Material's original behavior.
    control.addEventListener("click", function (event) {
      if (event.detail === 0) {
        event.preventDefault();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", enableConsentSettingsKeyboard);
  } else {
    enableConsentSettingsKeyboard();
  }
})();

/* Preserve the native Material surfaces while giving their label triggers a
 * complete keyboard contract. These checkboxes control UI only, not consent. */
(function () {
  function enableHeaderKeyboard() {
    ["search", "drawer"].forEach(function (name) {
      var toggle = document.getElementById("__" + name);
      var trigger = document.querySelector('.md-header__button[for="__' + name + '"]');
      if (!toggle || !trigger) return;
      trigger.tabIndex = 0;
      trigger.setAttribute("role", "button");
      trigger.setAttribute("aria-label", name === "search" ? "Search" : "Open navigation");
      trigger.setAttribute("aria-expanded", String(toggle.checked));
      function sync() { trigger.setAttribute("aria-expanded", String(toggle.checked)); }
      toggle.addEventListener("change", sync);
      trigger.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        event.stopPropagation();
        toggle.checked = !toggle.checked;
        toggle.dispatchEvent(new Event("change", { bubbles: true }));
      });
      document.addEventListener("keydown", function (event) {
        if (event.key !== "Escape" || event.defaultPrevented || !toggle.checked) return;
        // Search is registered first and owns Escape when both surfaces are
        // open; do not close the underlying drawer or steal its focus.
        event.preventDefault();
        toggle.checked = false;
        toggle.dispatchEvent(new Event("change", { bubbles: true }));
        // Material may blur its query field later in the same event turn.
        requestAnimationFrame(function () {
          if (!toggle.checked) trigger.focus({ preventScroll: true });
        });
      }, true);
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", enableHeaderKeyboard, { once: true });
  else enableHeaderKeyboard();
})();
