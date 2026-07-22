/**
 * chat.js — Interfaz del chat de Licencias & GRC
 * Renderiza respuestas en markdown usando marked.js (CDN).
 */
(function () {
  "use strict";

  var messagesEl   = document.getElementById("chatMessages");
  var inputEl      = document.getElementById("chatInput");
  var sendBtn      = document.getElementById("chatSendBtn");
  var clearBtn     = document.getElementById("chatClearBtn");
  var emptyEl      = document.getElementById("chatEmpty");
  var suggestionsEl = document.getElementById("chatSuggestions");

  /* ── marked.js config ─────────────────────────────────────────────── */
  if (window.marked) {
    marked.setOptions({ breaks: true, gfm: true });
  }

  function renderMarkdown(text) {
    if (window.marked) {
      return marked.parse(text);
    }
    // Fallback: escapar HTML y respetar saltos de línea
    return text
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/\n/g, "<br>");
  }

  /* ── Scroll ────────────────────────────────────────────────────────── */
  function scrollBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  /* ── Agregar burbuja ───────────────────────────────────────────────── */
  function addBubble(role, html, isHtml) {
    if (emptyEl) emptyEl.style.display = "none";
    if (suggestionsEl) suggestionsEl.style.display = "none";

    var div = document.createElement("div");
    div.className = "chatBubble chatBubble--" + role;
    if (isHtml) {
      div.innerHTML = html;
    } else {
      div.textContent = html;
    }
    messagesEl.appendChild(div);
    scrollBottom();
    return div;
  }

  /* ── Indicador de escritura ────────────────────────────────────────── */
  function addTyping() {
    var div = document.createElement("div");
    div.className = "chatTyping";
    div.id = "chatTypingIndicator";
    div.innerHTML = "<span></span><span></span><span></span>";
    messagesEl.appendChild(div);
    scrollBottom();
    return div;
  }

  function removeTyping() {
    var el = document.getElementById("chatTypingIndicator");
    if (el) el.remove();
  }

  /* ── Enviar pregunta ───────────────────────────────────────────────── */
  function send() {
    var pregunta = inputEl.value.trim();
    if (!pregunta) return;

    addBubble("user", pregunta, false);
    inputEl.value = "";
    inputEl.style.height = "auto";
    sendBtn.disabled = true;
    addTyping();

    fetch("/chat/mensaje", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pregunta: pregunta }),
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        removeTyping();
        var texto = data.respuesta || data.error || "Sin respuesta.";
        addBubble("assistant", renderMarkdown(texto), true);
      })
      .catch(function (err) {
        removeTyping();
        addBubble("assistant", "⚠️ Error de conexión. Intentá de nuevo.", false);
      })
      .finally(function () {
        sendBtn.disabled = false;
        inputEl.focus();
      });
  }

  /* ── Eventos ───────────────────────────────────────────────────────── */
  sendBtn.addEventListener("click", send);

  inputEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });

  // Auto-resize textarea
  inputEl.addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(this.scrollHeight, 120) + "px";
  });

  // Limpiar historial
  clearBtn.addEventListener("click", function () {
    fetch("/chat/limpiar", { method: "POST" })
      .then(function () {
        messagesEl.innerHTML = "";
        if (emptyEl) emptyEl.style.display = "";
        if (suggestionsEl) suggestionsEl.style.display = "";
        inputEl.focus();
      });
  });

  // Sugerencias de preguntas
  document.querySelectorAll(".chatSuggestion").forEach(function (btn) {
    btn.addEventListener("click", function () {
      inputEl.value = btn.getAttribute("data-q");
      inputEl.dispatchEvent(new Event("input"));
      send();
    });
  });

})();
