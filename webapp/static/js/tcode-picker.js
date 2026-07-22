/**
 * Picker de TCodes con busqueda/autocompletado + chips.
 * Replica tcPickerLoadData/tcPickerSearch/tcPickerKey de
 * SAP_SOD_Analyzer_v640.html, adaptado a la lista de TCodes que expone
 * el backend (GET /sod/reglas/tcodes.json) en lugar de leerla de SQLite
 * en el navegador.
 *
 * Marcado esperado (ver templates/sod/rule_form.html):
 *
 *   <div data-tc-picker data-tc-source="{{ url }}" data-tc-initial="ME21N,ME22N">
 *     <div data-tc-tags></div>
 *     <div>
 *       <input data-tc-search>
 *       <div data-tc-drop></div>
 *     </div>
 *     <input type="hidden" name="tcodes1" data-tc-hidden>
 *   </div>
 */
(function () {
  "use strict";

  var cache = {};

  function escapeHtml(text) {
    return String(text || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // El backend devuelve [{tcode, descripcion}, ...] o [string, ...].
  function fetchTcodes(url, callback) {
    if (!url) { callback([]); return; }
    if (cache[url]) { callback(cache[url]); return; }
    fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (data) {
        cache[url] = Array.isArray(data) ? data : [];
        callback(cache[url]);
      })
      .catch(function () { callback([]); });
  }

  function debounce(fn, ms) {
    var t;
    return function () {
      clearTimeout(t);
      t = setTimeout(fn, ms);
    };
  }

  function initPicker(root) {
    var source   = root.getAttribute("data-tc-source") || "";
    var initial  = (root.getAttribute("data-tc-initial") || "")
      .split(",").map(function (s) { return s.trim().toUpperCase(); }).filter(Boolean);

    var tagsEl   = root.querySelector("[data-tc-tags]");
    var searchEl = root.querySelector("[data-tc-search]");
    var dropEl   = root.querySelector("[data-tc-drop]");
    var hiddenEl = root.querySelector("[data-tc-hidden]");

    var selected    = [];
    var allTcodes   = [];
    var descByTcode = {};
    var activeEl    = null;   // referencia directa al item activo (no índice global)

    /* ── helpers ─────────────────────────────────────────────────────── */

    function syncHidden() {
      if (hiddenEl) hiddenEl.value = selected.join(",");
    }

    function hideDrop() {
      dropEl.classList.add("isHidden");
      dropEl.innerHTML = "";
      activeEl = null;
    }

    /** Cambia el item activo actualizando solo 2 nodos en el DOM. */
    function setActive(el) {
      if (activeEl) activeEl.classList.remove("tcPicker__dropItem--active");
      activeEl = el || null;
      if (activeEl) {
        activeEl.classList.add("tcPicker__dropItem--active");
        activeEl.scrollIntoView({ block: "nearest" });
      }
    }

    /* ── tags ─────────────────────────────────────────────────────────── */

    function addTag(raw) {
      var tc = (raw || "").trim().toUpperCase();
      if (!tc || selected.indexOf(tc) !== -1) return;
      selected.push(tc);

      var tag = document.createElement("span");
      tag.className = "tcPicker__tag";
      tag.setAttribute("data-tc", tc);
      tag.appendChild(document.createTextNode(tc));

      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "tcPicker__tagRemove";
      btn.setAttribute("aria-label", "Quitar " + tc);
      btn.textContent = "×";
      btn.addEventListener("click", function () { removeTag(tc); });
      tag.appendChild(btn);

      tagsEl.appendChild(tag);
      searchEl.value = "";
      hideDrop();
      syncHidden();
    }

    function removeTag(tc) {
      selected = selected.filter(function (x) { return x !== tc; });
      var el = tagsEl.querySelector('[data-tc="' + tc + '"]');
      if (el) el.remove();
      syncHidden();
    }

    /* ── dropdown ─────────────────────────────────────────────────────── */

    function renderDrop(matches, query) {
      activeEl = null;

      if (!matches.length && !query) {
        dropEl.innerHTML = '<div class="tcPicker__dropEmpty">' +
          (allTcodes.length
            ? "Todos los roles ya están seleccionados"
            : "Escribí el rol y presioná Enter para agregarlo") +
          "</div>";
        dropEl.classList.remove("isHidden");
        return;
      }

      /* Construir con fragment para un solo reflow */
      var frag = document.createDocumentFragment();

      matches.forEach(function (tc) {
        var desc    = descByTcode[tc] || "";
        var item    = document.createElement("div");
        item.className = "tcPicker__dropItem";
        item.setAttribute("data-tc", tc);

        var tcSpan = document.createElement("span");
        tcSpan.className = "tcPicker__dropTc";
        tcSpan.textContent = tc;
        item.appendChild(tcSpan);

        if (desc) {
          var dSpan = document.createElement("span");
          dSpan.className = "tcPicker__dropDesc";
          dSpan.textContent = desc;
          item.appendChild(dSpan);
        }
        frag.appendChild(item);
      });

      if (query && allTcodes.indexOf(query) === -1 && selected.indexOf(query) === -1) {
        var custom = document.createElement("div");
        custom.className = "tcPicker__dropItem tcPicker__dropItem--custom";
        custom.setAttribute("data-tc", query);
        custom.textContent = '+ Agregar "' + query + '" manualmente';
        frag.appendChild(custom);
      }

      dropEl.innerHTML = "";
      if (!frag.childNodes.length) {
        dropEl.innerHTML = '<div class="tcPicker__dropEmpty">Sin resultados</div>';
      } else {
        dropEl.appendChild(frag);
      }
      dropEl.classList.remove("isHidden");
    }

    function search() {
      var q         = (searchEl.value || "").trim().toUpperCase();
      var available = allTcodes.filter(function (t) { return selected.indexOf(t) === -1; });
      var matches;

      if (!q) {
        matches = available.slice(0, 60);
      } else {
        var starts   = [];
        var contains = [];
        var byDesc   = [];
        for (var i = 0; i < available.length; i++) {
          var t = available[i];
          if (t.indexOf(q) === 0) {
            starts.push(t);
          } else if (t.indexOf(q) !== -1) {
            contains.push(t);
          } else if ((descByTcode[t] || "").toUpperCase().indexOf(q) !== -1) {
            byDesc.push(t);
          }
          if (starts.length + contains.length >= 80) break;
        }
        matches = starts.concat(contains).concat(byDesc).slice(0, 80);
      }

      renderDrop(matches, q);
    }

    var debouncedSearch = debounce(search, 120);

    /* ── eventos dropdown ─────────────────────────────────────────────── */

    dropEl.addEventListener("mousedown", function (event) {
      var item = event.target.closest("[data-tc]");
      if (!item) return;
      event.preventDefault();
      addTag(item.getAttribute("data-tc"));
    });

    /* mouseover: solo 2 actualizaciones de clase, sin querySelectorAll */
    dropEl.addEventListener("mouseover", function (event) {
      var item = event.target.closest(".tcPicker__dropItem");
      if (!item || item === activeEl) return;
      setActive(item);
    });

    /* ── eventos input ────────────────────────────────────────────────── */

    searchEl.addEventListener("input", debouncedSearch);
    searchEl.addEventListener("focus", search);   // focus sin debounce (el usuario espera)
    searchEl.addEventListener("blur", function () {
      setTimeout(hideDrop, 180);
    });

    searchEl.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        var next = activeEl
          ? activeEl.nextElementSibling
          : dropEl.querySelector(".tcPicker__dropItem");
        if (next && next.classList.contains("tcPicker__dropItem")) setActive(next);

      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        var prev = activeEl && activeEl.previousElementSibling;
        if (prev && prev.classList.contains("tcPicker__dropItem")) setActive(prev);

      } else if (event.key === "Enter" || event.key === ",") {
        event.preventDefault();
        if (activeEl) {
          addTag(activeEl.getAttribute("data-tc"));
        } else if (searchEl.value.trim()) {
          addTag(searchEl.value);
        }

      } else if (event.key === "Backspace" && !searchEl.value && selected.length) {
        removeTag(selected[selected.length - 1]);

      } else if (event.key === "Escape") {
        hideDrop();
      }
    });

    /* ── init ─────────────────────────────────────────────────────────── */

    initial.forEach(function (tc) { addTag(tc); });

    fetchTcodes(source, function (list) {
      allTcodes = list.map(function (item) {
        return typeof item === "string" ? item : item.tcode;
      });
      descByTcode = {};
      list.forEach(function (item) {
        if (item && typeof item === "object" && item.descripcion) {
          descByTcode[item.tcode] = item.descripcion;
        }
      });
      searchEl.placeholder = allTcodes.length
        ? "Buscar entre " + allTcodes.length + " roles..."
        : "Escribí el rol manualmente...";
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-tc-picker]").forEach(initPicker);
  });
})();
