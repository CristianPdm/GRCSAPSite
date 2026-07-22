/**
 * Busqueda incremental (en vivo, sin recargar la pagina) para la Matriz
 * Usuario x Rol x TCode. El filtro sigue siendo server-side (la matriz
 * puede tener decenas de miles de filas, no se cargan todas al navegador
 * como en el original) -- esto solo evita el viaje de pagina completa:
 * mientras se escribe se pide al servidor (GET /sod/matriz con header
 * X-Requested-With) solo el fragmento de resultados y se reemplaza
 * #mx-results, sin perder el foco del campo de texto.
 *
 * Marcado esperado (ver templates/sod/matriz.html):
 *
 *   <form data-mx-filter-form data-mx-source="{{ url_for('sod.matriz') }}">
 *     <input data-mx-field="u" name="u">
 *     <input data-mx-field="r" name="r">
 *     <input data-mx-field="t" name="t">
 *   </form>
 *   {% include "sod/_matriz_rows.html" %}   <!-- contiene #mx-results -->
 *
 * Si JS esta deshabilitado, el form sigue funcionando como GET normal
 * (boton "Filtrar"), que es el fallback que ya tenia la pagina.
 */
(function () {
  "use strict";

  var DEBOUNCE_MS = 350;

  function initForm(form) {
    var source = form.getAttribute("data-mx-source") || form.action;
    var fields = Array.prototype.slice.call(form.querySelectorAll("[data-mx-field]"));
    var timer = null;
    var activeFetch = null;

    function buildUrl() {
      var params = new URLSearchParams();
      fields.forEach(function (field) {
        var value = (field.value || "").trim();
        if (value) params.set(field.getAttribute("data-mx-field"), value);
      });
      var query = params.toString();
      return query ? source + "?" + query : source;
    }

    function refresh(options) {
      var isSubmit = !!(options && options.isSubmit);
      var url = buildUrl();
      var resultsEl = document.getElementById("mx-results");
      if (resultsEl) resultsEl.classList.add("mxResults--loading");

      if (activeFetch) activeFetch.cancelled = true;
      var thisFetch = { cancelled: false };
      activeFetch = thisFetch;

      fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (response) {
          if (!response.ok) throw new Error("HTTP " + response.status);
          return response.text();
        })
        .then(function (html) {
          if (thisFetch.cancelled) return;
          var current = document.getElementById("mx-results");
          if (current) current.outerHTML = html.trim();
          window.history.replaceState(null, "", url);
        })
        .catch(function (err) {
          if (thisFetch.cancelled) return;
          // No fallar en silencio: se ve en la consola para poder diagnosticar,
          // y si el usuario filtro explicitamente (boton/Enter) se recurre a
          // una recarga de pagina completa con el mismo filtro -- el
          // comportamiento de siempre, sin JS, que sirve de respaldo si la
          // busqueda incremental por algun motivo falla (red, sesion, error
          // de servidor).
          console.error("matrix-filters: fallo la busqueda incremental", err);
          if (isSubmit) window.location.href = url;
        })
        .finally(function () {
          var el = document.getElementById("mx-results");
          if (el) el.classList.remove("mxResults--loading");
        });
    }

    fields.forEach(function (field) {
      // "input" cubre los campos de texto; "change" se agrega para el
      // <select> de exclusion de dimension (Sin usuario/rol/tcode), que en
      // algunos navegadores no dispara "input" al elegir una opcion.
      var trigger = function () {
        if (timer) window.clearTimeout(timer);
        timer = window.setTimeout(refresh, DEBOUNCE_MS);
      };
      field.addEventListener("input", trigger);
      field.addEventListener("change", trigger);
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      if (timer) window.clearTimeout(timer);
      refresh({ isSubmit: true });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-mx-filter-form]").forEach(initForm);
  });
})();
