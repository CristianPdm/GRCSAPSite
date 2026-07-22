/**
 * Filtro/busqueda generico en cliente para listados (filas de tabla o
 * tarjetas/grupos), data-driven y sin handlers inline en las plantillas.
 *
 * Marcado esperado:
 *
 *   <div class="filterBar" data-filter-scope="ALGO">
 *     <input data-filter-field="q" ...>
 *     <select data-filter-field="modulo">...</select>
 *   </div>
 *
 *   <table id="ALGO"> ... <tbody>
 *     <tr data-filter-item data-f-q="texto buscable en minusculas"
 *         data-f-modulo="MM" data-f-nivel="CRITICO">...</tr>
 *   </tbody></table>
 *   <tr id="ALGO-empty" class="filterBar__emptyRow isHidden">
 *     <td colspan="...">Sin resultados para el filtro.</td>
 *   </tr>
 *
 * El campo "q" hace busqueda por substring (contiene); el resto de los
 * campos (modulo, nivel, estado, fue, etc.) hace coincidencia exacta por
 * defecto. Para filtros de "umbral" (ej. dias de inactividad >30/90/180),
 * donde un mismo item puede calificar para varios valores del select, se
 * puede agregar data-filter-mode="token" al <select>: el item declara
 * todos los valores que cumple separados por espacios
 * (data-f-ia="30 90 180") y el filtro hace coincidencia de palabra
 * completa en esa lista, no substring crudo.
 * El contenedor de items no tiene que ser el mismo id que la tabla: si no
 * existe un elemento con ese id se usa document como fallback.
 */
(function () {
  "use strict";

  function normalize(value) {
    return (value || "").toString().trim().toLowerCase();
  }

  function readFilters(bar) {
    var filters = {};
    bar.querySelectorAll("[data-filter-field]").forEach(function (el) {
      var field = el.getAttribute("data-filter-field");
      var value = normalize(el.value);
      if (value) {
        filters[field] = { value: value, mode: el.getAttribute("data-filter-mode") || "" };
      }
    });
    return filters;
  }

  function matchesItem(item, filters) {
    for (var field in filters) {
      if (!Object.prototype.hasOwnProperty.call(filters, field)) continue;
      var f = filters[field];
      var have = normalize(item.getAttribute("data-f-" + field));
      if (field === "q" || f.mode === "contains") {
        if (have.indexOf(f.value) === -1) return false;
      } else if (f.mode === "token") {
        if ((" " + have + " ").indexOf(" " + f.value + " ") === -1) return false;
      } else if (have !== f.value) {
        return false;
      }
    }
    return true;
  }

  function applyFilters(scope) {
    var bar = document.querySelector('[data-filter-scope="' + scope + '"]');
    if (!bar) return;

    var container = document.getElementById(scope) || document;
    var items = container.querySelectorAll("[data-filter-item]");
    var filters = readFilters(bar);

    var visibleCount = 0;
    items.forEach(function (item) {
      var visible = matchesItem(item, filters);
      item.classList.toggle("isHidden", !visible);
      if (visible) visibleCount += 1;
    });

    var emptyEl = document.getElementById(scope + "-empty");
    if (emptyEl) {
      emptyEl.classList.toggle("isHidden", items.length === 0 || visibleCount !== 0);
    }

    var countEl = document.getElementById(scope + "-count");
    if (countEl) {
      countEl.textContent = visibleCount + " de " + items.length;
    }
  }

  function scopeFromField(el) {
    var bar = el.closest("[data-filter-scope]");
    return bar ? bar.getAttribute("data-filter-scope") : null;
  }

  function debounce(fn, delay) {
    var timer;
    return function () {
      var args = arguments;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(null, args); }, delay);
    };
  }

  // 'input' (texto libre): debounce 200 ms para no recalcular en cada tecla.
  // 'change' (selects): inmediato, el usuario ya eligio el valor final.
  var debouncedApply = debounce(applyFilters, 200);

  document.addEventListener("input", function (event) {
    var field = event.target.closest("[data-filter-field]");
    if (!field) return;
    var scope = scopeFromField(field);
    if (scope) debouncedApply(scope);
  });

  document.addEventListener("change", function (event) {
    var field = event.target.closest("[data-filter-field]");
    if (!field) return;
    var scope = scopeFromField(field);
    if (scope) applyFilters(scope);
  });

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-filter-scope]").forEach(function (bar) {
      applyFilters(bar.getAttribute("data-filter-scope"));
    });
  });
})();
