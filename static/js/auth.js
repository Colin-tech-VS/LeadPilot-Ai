document.documentElement.classList.add("js-enabled");

(function () {
  var form = document.getElementById("register-form");
  if (!form) return;

  var password = document.getElementById("password");
  var confirm = document.getElementById("confirm_password");

  function syncValidity() {
    if (!password || !confirm) return;
    if (confirm.value && password.value !== confirm.value) {
      confirm.setCustomValidity("Les mots de passe ne correspondent pas.");
    } else {
      confirm.setCustomValidity("");
    }
  }

  if (password) password.addEventListener("input", syncValidity);
  if (confirm) confirm.addEventListener("input", syncValidity);

  form.addEventListener("submit", function () {
    syncValidity();
    if (confirm && !confirm.checkValidity()) {
      confirm.reportValidity();
    }
  });
})();
