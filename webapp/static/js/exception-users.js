/**
 * Pobla el select "Usuario SAP" del formulario de excepciones (ver
 * templates/sod/exceptions.html) segun la regla elegida en "Regla".
 *
 * Los datos (regla_id -> lista de usuarios con conflicto activo, sin
 * excepcion todavia) vienen embebidos por el backend en
 * #excepcionesUsersByRule, calculados una sola vez con run_analysis()
 * al renderizar la pagina, sin requests adicionales.
 *
 * Marcado esperado:
 *   <select id="regla_id">...</select>
 *   <select id="usuario">...</select>
 *   <script type="application/json" id="excepcionesUsersByRule">{...}</script>
 */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var ruleSelect = document.getElementById("regla_id");
    var userSelect = document.getElementById("usuario");
    var dataEl = document.getElementById("excepcionesUsersByRule");
    if (!ruleSelect || !userSelect || !dataEl) return;

    var usersByRule = {};
    try {
      usersByRule = JSON.parse(dataEl.textContent || "{}");
    } catch (err) {
      usersByRule = {};
    }

    function renderUsers() {
      var users = usersByRule[ruleSelect.value] || [];
      userSelect.innerHTML = "";

      if (!users.length) {
        var emptyOpt = document.createElement("option");
        emptyOpt.value = "";
        emptyOpt.textContent = "Sin usuarios en conflicto activo para esta regla";
        userSelect.appendChild(emptyOpt);
        userSelect.disabled = true;
        return;
      }

      userSelect.disabled = false;

      var placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "Selecciona un usuario...";
      userSelect.appendChild(placeholder);

      users.forEach(function (username) {
        var opt = document.createElement("option");
        opt.value = username;
        opt.textContent = username;
        userSelect.appendChild(opt);
      });
    }

    ruleSelect.addEventListener("change", renderUsers);
    renderUsers();
  });
})();
