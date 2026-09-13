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
      control.click();
      control.setAttribute("aria-expanded", String(settings.checked));
    });

    settings.addEventListener("change", function () {
      control.setAttribute("aria-expanded", String(settings.checked));
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", enableConsentSettingsKeyboard);
  } else {
    enableConsentSettingsKeyboard();
  }
})();
