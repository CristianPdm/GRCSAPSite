"""Rutas del módulo de chat: interfaz conversacional con Claude (Anthropic API)
usando tool use para consultar datos SAP en tiempo real."""
import json
import uuid
from datetime import datetime

from flask import current_app, jsonify, render_template, request, session
from flask_login import current_user, login_required

from app.decorators import permission_required
from app.chat import bp
from app.chat.tools import TOOL_FUNCTIONS, TOOLS_DEFINITION

# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Sos un asistente especializado en análisis de licencias SAP y gestión de \
riesgos SOD (Segregación de Funciones) para la instalación SAP S/4HANA de la empresa.

Tenés acceso a herramientas que consultan la base de datos en tiempo real con datos reales \
del sistema. Usá las herramientas para obtener datos exactos antes de responder — nunca \
inventes información sobre usuarios, roles o transacciones.

**Contexto del sistema de licenciamiento FUE (Full Use Equivalent):**
- **ADV** (GB Advanced Use): peso 1.0 — la licencia más cara, para usuarios de procesos complejos
- **CORE** (GC Core Use): peso 0.2 — un quinto de ADV, para usuarios de procesos estándar
- **SELF** (GD Self-Service Use): peso 1/30 ≈ 0.033 — la más económica, para aprobaciones simples
- **Sin tipo FUE**: peso 0.0 — usuarios técnicos o de sistema, sin costo de licencia

El FUE "oficial" es el que SAP factura (viene de FUE_Users.xlsx importado). El FUE "derivado" \
es el que el sistema calcula en base a los roles activos del usuario — toma el FUE más alto \
de todos sus roles clasificados en FUE_Rol.xlsx. Las simulaciones trabajan sobre el FUE derivado.

**Reglas para responder:**
- Siempre usá las herramientas para obtener datos reales
- Respondé en español, de forma concisa y directa
- Usá números concretos; cuando haya listas largas, mostrá los más relevantes y mencioná el total
- Para simulaciones, explicá claramente el supuesto y el ahorro estimado
- Si no hay datos importados (la herramienta devuelve totales en 0), indicarlo claramente
- Usá markdown para tablas y listas cuando mejore la legibilidad"""

# ── Helpers de conversación ───────────────────────────────────────────────────

MAX_HISTORY = 12  # máximo de mensajes (pares usuario/asistente) en sesión


def _get_history() -> list[dict]:
    return session.get("chat_history", [])


def _save_history(messages: list[dict]):
    # Guardar solo mensajes de texto (sin bloques tool_use/tool_result)
    # para no superar el límite de la cookie de sesión
    clean = []
    for m in messages:
        if m["role"] == "user":
            content = m["content"]
            if isinstance(content, list):
                # Solo bloques de texto
                text_parts = [b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"]
                if text_parts:
                    clean.append({"role": "user", "content": text_parts[0]})
            else:
                clean.append({"role": "user", "content": str(content)})
        elif m["role"] == "assistant":
            content = m["content"]
            if isinstance(content, list):
                text_parts = [b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"]
                if text_parts:
                    clean.append({"role": "assistant", "content": text_parts[0]})
            else:
                clean.append({"role": "assistant", "content": str(content)})

    # Mantener solo los últimos MAX_HISTORY mensajes
    session["chat_history"] = clean[-MAX_HISTORY:]


# ── Dispatcher de herramientas ────────────────────────────────────────────────

def _execute_tool(name: str, inputs: dict) -> str:
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return json.dumps({"error": f"Herramienta '{name}' no encontrada."})
    try:
        result = fn(**inputs)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"Error al ejecutar {name}: {str(exc)}"})


# ── Agentic loop ──────────────────────────────────────────────────────────────

def _call_claude(messages: list[dict]) -> tuple[str, list[dict]]:
    """Llama a la API de Claude con tool use. Ejecuta las herramientas que
    Claude pida y vuelve a llamar hasta obtener una respuesta de texto.
    Devuelve (texto_final, messages_actualizados)."""
    import anthropic

    api_key = current_app.config.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ("⚠️ La API key de Anthropic no está configurada. "
                "Agregar ANTHROPIC_API_KEY al archivo .env del servidor."), messages

    client = anthropic.Anthropic(api_key=api_key)
    model = current_app.config.get("CHAT_MODEL", "claude-haiku-4-5-20251001")

    MAX_ROUNDS = 5  # evitar loops infinitos
    for _ in range(MAX_ROUNDS):
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOLS_DEFINITION,
            messages=messages,
        )

        # Agregar respuesta del asistente al historial de la llamada
        messages = messages + [{"role": "assistant", "content": response.content}]

        if response.stop_reason == "end_turn":
            # Respuesta final de texto
            text = "".join(
                block.text for block in response.content
                if hasattr(block, "text")
            )
            return text, messages

        if response.stop_reason == "tool_use":
            # Ejecutar todas las herramientas solicitadas
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result_text = _execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    })

            # Agregar resultados al historial de la llamada y continuar
            messages = messages + [{"role": "user", "content": tool_results}]
            continue

        break  # stop_reason inesperado

    return "No pude generar una respuesta. Intentá reformular la pregunta.", messages


# ── Rutas ─────────────────────────────────────────────────────────────────────

@bp.route("/chat")
@login_required
@permission_required("can_view_chat")
def index():
    return render_template("chat/index.html")


@bp.route("/chat/mensaje", methods=["POST"])
@login_required
@permission_required("can_view_chat")
def mensaje():
    data = request.get_json(silent=True) or {}
    pregunta = (data.get("pregunta") or "").strip()
    if not pregunta:
        return jsonify({"error": "Pregunta vacía."}), 400

    # Cargar historial y agregar mensaje del usuario
    history = _get_history()
    api_messages = history + [{"role": "user", "content": pregunta}]

    # Llamar a Claude
    respuesta, updated_messages = _call_claude(api_messages)

    # Guardar historial compacto en sesión
    full_for_save = history + [
        {"role": "user", "content": pregunta},
        {"role": "assistant", "content": respuesta},
    ]
    _save_history(full_for_save)

    return jsonify({"respuesta": respuesta})


@bp.route("/chat/limpiar", methods=["POST"])
@login_required
@permission_required("can_view_chat")
def limpiar():
    session.pop("chat_hi