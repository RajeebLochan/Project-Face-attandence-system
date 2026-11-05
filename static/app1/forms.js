/* Prevent double submission: a form marked data-once disables its submit buttons after the
   first submit. The server is idempotent anyway; this just avoids confusing repeats. */
(function () {
  "use strict";
  document.querySelectorAll("form[data-once]").forEach(function (form) {
    form.addEventListener("submit", function () {
      if (form.dataset.submitted) return;
      form.dataset.submitted = "1";
      window.setTimeout(function () {
        form.querySelectorAll("button[type=submit]").forEach(function (b) {
          b.disabled = true;
          b.setAttribute("aria-busy", "true");
        });
      }, 0);
    });
  });
  document.querySelectorAll("[data-print]").forEach(function (button) {
    button.addEventListener("click", function () { window.print(); });
  });
})();
