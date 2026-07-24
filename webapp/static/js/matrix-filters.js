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

  var DEBOUNCE_MS = 400;
  // Minimo de caracteres para disparar busqueda incremental (no en submit)
  var MIN_CHARS = 2;

  // ── Plantillas de estado ──────────────────────────────────────────────────

  function loadingHTML() {
    return '<div id="mx-results" class="mxLoading" aria-live="polite" aria-busy="true">' +
      '<span class="mxLoading__spinner" aria-hidden="true"></span>' +
      '<span class="mxLoading__text">Procesando…</span>' +
      '</div>';
  }

  function hintHTML() {
    return '<div id="mx-results"><p class="mxEmpty">' +
      'Ingresá al menos ' + MIN_CHARS + ' caracteres en un campo para buscar.' +
      '</p></div>';
  }

  function replaceResults(html) {
    var el = document.getElementById("mx-results");
    if (el) el.outerHTML = html;
  }

  // ── Logica principal ──────────────────────────────────────────────────────

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

    function hasEnoughInput(minChars) {
      return fields.some(function (f) {
        return (f.value || "").trim().length >= minChars;
      });
    }

    function doFetch(url, isSubmit) {
      // Cancelar fetch anterior
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
          replaceResults(html.trim());
          window.history.replaceState(null, "", url);
        })
        .catch(function (err) {
          if (thisFetch.cancelled) return;
          console.error("matrix-filters: fallo la busqueda incremental", err);
          if (isSubmit) window.location.href = url;
          else {
            var el = document.getElementById("mx-results");
            if (el) el.classList.remove("mxLoading");
          }
        });
    }

    function refresh(options) {
      var isSubmit = !!(options && options.isSubmit);

      // Busqueda incremental: esperar minimo de caracteres
      if (!isSubmit && !hasEnoughInput(MIN_CHARS)) {
        replaceResults(hintHTML());
        return;
      }

      // Limpiar inmediatamente y mostrar spinner antes del viaje al servidor
      replaceResults(loadingHTML());

      doFetch(buildUrl(), isSubmit);
    }

    // QA 3.a: busqueda incremental solo en inputs de texto (no en <select>).
    // Los selects solo tienen efecto al presionar "Filtrar".
    fields.forEach(function (field) {
      if (field.tagName.toLowerCase() === "input") {
        var trigger = function () {
          if (timer) window.clearTimeout(timer);
          timer = window.setTimeout(refresh, DEBOUNCE_MS);
        };
        field.addEventListener("input", trigger);
      }
    });

    // Boton "Filtrar" / Enter: sin debounce, limpia y busca de inmediato
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
