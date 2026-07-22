"""Herramientas del chat: funciones Python que Claude puede invocar para
consultar la base de datos y ejecutar simulaciones.

Cada función devuelve un dict serializable a JSON — nunca objetos ORM.
"""
from datetime import date

from sqlalchemy import or_

from app.extensions import db
from app.sod.models import SapRoleAssignment, SapRoleTcode, SapRoleDescription
from app.licenses.models import LicenseRole, LicenseUser
from app.licenses.rules import FUE_WEIGHT, FUE_ORDER, FUE_LABEL


# ── Helpers internos ─────────────────────────────────────────────────────────

def _active_assignments():
    """Filtro base: asignaciones rol→usuario vigentes a hoy."""
    today = date.today()
    return SapRoleAssignment.query.filter(
        or_(SapRoleAssignment.valid_to.is_(None), SapRoleAssignment.valid_to >= today)
    )


def _roles_of_user(username: str) -> list[str]:
    """Lista de nombres de rol activos para un usuario (incluyendo la jerarquía
    de roles compuestos, igual que el motor SOD)."""
    from app.sod.engine import _build_children_map, _expand_role
    children_map = _build_children_map()
    rows = _active_assignments().filter_by(username=username).all()
    roles = set()
    for row in rows:
        for r in _expand_role(row.role_name, children_map):
            roles.add(r)
    return sorted(roles)


# ── Herramientas expuestas a Claude ──────────────────────────────────────────

def buscar_usuarios_con_transaccion(tcode: str) -> dict:
    """Devuelve todos los usuarios que tienen acceso a la transacción indicada
    a través de alguno de sus roles activos."""
    tcode = tcode.strip().upper()

    # Roles que tienen el tcode
    roles_con_tc = {
        r.role_name
        for r in SapRoleTcode.query.filter_by(tcode=tcode).all()
    }
    if not roles_con_tc:
        return {"tcode": tcode, "usuarios": [], "total": 0,
                "mensaje": f"La transacción {tcode} no está asociada a ningún rol."}

    # Usuarios con alguno de esos roles activos
    usuarios = set()
    rows = _active_assignments().filter(
        SapRoleAssignment.role_name.in_(list(roles_con_tc))
    ).all()
    for row in rows:
        usuarios.add(row.username)

    return {
        "tcode": tcode,
        "roles_con_acceso": len(roles_con_tc),
        "usuarios": sorted(usuarios),
        "total": len(usuarios),
    }


def buscar_roles_con_transaccion(tcode: str) -> dict:
    """Devuelve todos los roles que otorgan acceso a la transacción indicada."""
    tcode = tcode.strip().upper()
    roles = sorted({r.role_name for r in SapRoleTcode.query.filter_by(tcode=tcode).all()})
    return {"tcode": tcode, "roles": roles, "total": len(roles)}


def buscar_roles_de_usuario(username: str) -> dict:
    """Devuelve los roles activos asignados a un usuario (con fecha de fin si tiene)."""
    username = username.strip().upper()
    today = date.today()
    rows = _active_assignments().filter_by(username=username).all()
    if not rows:
        return {"username": username, "roles": [], "total": 0,
                "mensaje": f"El usuario {username} no tiene roles activos."}

    roles = []
    for r in rows:
        roles.append({
            "rol": r.role_name,
            "valido_hasta": r.valid_to.isoformat() if r.valid_to else "Sin vencimiento",
        })
    roles.sort(key=lambda x: x["rol"])
    return {"username": username, "roles": roles, "total": len(roles)}


def buscar_usuarios_de_rol(role_name: str) -> dict:
    """Devuelve todos los usuarios con el rol activo indicado."""
    role_name = role_name.strip()
    rows = _active_assignments().filter_by(role_name=role_name).all()
    if not rows:
        return {"rol": role_name, "usuarios": [], "total": 0,
                "mensaje": f"No hay usuarios con el rol {role_name}."}

    usuarios = sorted({r.username for r in rows})
    return {"rol": role_name, "usuarios": usuarios, "total": len(usuarios)}


def obtener_conflictos_sod_usuario(username: str) -> dict:
    """Devuelve los conflictos SOD detectados para un usuario específico."""
    from app.sod.engine import build_maps, run_analysis
    from app.sod.models import SodException

    username = username.strip().upper()
    tc2r, r2u = build_maps()

    # Filtrar el análisis solo para este usuario
    conflicts = []
    analysis = run_analysis(tc2r, r2u)
    exceptions = {
        e.regla_id
        for e in SodException.query.filter_by(usuario=username).all()
    }

    for item in analysis:
        rule = item["rule"]
        users = item.get("users", set())
        if username not in users:
            continue
        if rule.id in exceptions:
            continue
        conflicts.append({
            "regla": rule.id,
            "modulo": rule.modulo,
            "nivel": rule.nivel,
            "descripcion": rule.descripcion,
        })

    conflicts.sort(key=lambda x: (
        {"CRITICO": 0, "ALTO": 1, "MEDIO": 2}.get(x["nivel"], 3), x["regla"]
    ))

    return {
        "username": username,
        "conflictos": conflicts,
        "total": len(conflicts),
        "criticos": sum(1 for c in conflicts if c["nivel"] == "CRITICO"),
        "altos": sum(1 for c in conflicts if c["nivel"] == "ALTO"),
        "medios": sum(1 for c in conflicts if c["nivel"] == "MEDIO"),
    }


def resumen_conflictos_sod() -> dict:
    """Devuelve estadísticas globales de conflictos SOD: totales, por nivel
    y por módulo."""
    from app.sod.engine import get_sod_summary
    return get_sod_summary()


def resumen_licencias() -> dict:
    """Devuelve el resumen actual de licencias FUE: conteos por tipo y total
    de FUEs consumidos."""
    from app.licenses.engine import get_license_summary
    return get_license_summary()


def simular_cambio_fue_rol(role_name: str, nuevo_fue_type: str) -> dict:
    """Simula qué ocurriría con el FUE derivado de los usuarios si el rol
    indicado cambiara su clasificación FUE.

    No modifica la base de datos — es solo una proyección hipotética.

    El FUE "derivado" de un usuario es el tipo FUE más alto de todos sus
    roles clasificados. Si bajamos un rol a SELF, solo cambia el FUE derivado
    de los usuarios para quienes ese rol era su único rol de tipo más alto.
    """
    role_name = role_name.strip()
    nuevo_fue_type = nuevo_fue_type.strip().upper()

    # Mapear variantes de nombre comunes
    _alias = {"SELF SERVICE": "SELF", "ADVANCED": "ADV", "CORE USE": "CORE",
               "GB": "ADV", "GC": "CORE", "GD": "SELF"}
    nuevo_fue_type = _alias.get(nuevo_fue_type, nuevo_fue_type)

    if nuevo_fue_type not in FUE_WEIGHT:
        return {"error": f"Tipo FUE inválido '{nuevo_fue_type}'. Usar: ADV, CORE, SELF o NONE."}

    # FUE actual del rol
    role_lic = LicenseRole.query.filter_by(role_name=role_name).first()
    current_fue = (role_lic.fue_type_code if role_lic and role_lic.fue_type_code in FUE_WEIGHT
                   else "NONE")

    if current_fue == nuevo_fue_type:
        return {
            "rol": role_name,
            "mensaje": f"El rol ya tiene tipo FUE {FUE_LABEL.get(nuevo_fue_type)}. No habría cambio.",
        }

    # Usuarios con este rol activo
    from app.sod.engine import _build_children_map, _expand_role
    children_map = _build_children_map()

    # Los usuarios pueden tener el rol directamente o a través de un rol compuesto
    all_assignments = _active_assignments().all()

    # Construir mapa username → set de roles efectivos
    user_roles: dict[str, set[str]] = {}
    for asn in all_assignments:
        effective = _expand_role(asn.role_name, children_map)
        user_roles.setdefault(asn.username, set()).update(effective)

    # Solo usuarios que tienen el rol objetivo
    affected_users = [u for u, roles in user_roles.items() if role_name in roles]
    if not affected_users:
        return {
            "rol": role_name,
            "mensaje": f"No hay usuarios con el rol {role_name} actualmente.",
        }

    # Cargar FUE de todos los roles involucrados
    all_role_names = set()
    for roles in user_roles.values():
        all_role_names.update(roles)

    role_fue: dict[str, str] = {}
    for lic in LicenseRole.query.filter(LicenseRole.role_name.in_(list(all_role_names))).all():
        code = lic.fue_type_code if lic.fue_type_code in FUE_WEIGHT else "NONE"
        role_fue[lic.role_name] = code

    def max_fue(roles, override_role=None, override_type=None):
        best_code = "NONE"
        best_order = 0
        for r in roles:
            ftype = override_type if (r == override_role) else role_fue.get(r, "NONE")
            if FUE_ORDER.get(ftype, 0) > best_order:
                best_order = FUE_ORDER[ftype]
                best_code = ftype
        return best_code

    # Calcular cambios por usuario
    cambios = []
    total_antes = 0.0
    total_despues = 0.0

    for username in affected_users:
        roles = user_roles[username]
        fue_antes = max_fue(roles)
        fue_despues = max_fue(roles, override_role=role_name, override_type=nuevo_fue_type)

        w_antes = FUE_WEIGHT.get(fue_antes, 0.0)
        w_despues = FUE_WEIGHT.get(fue_despues, 0.0)
        total_antes += w_antes
        total_despues += w_despues

        if fue_antes != fue_despues:
            cambios.append({
                "usuario": username,
                "fue_antes": FUE_LABEL.get(fue_antes, fue_antes),
                "fue_despues": FUE_LABEL.get(fue_despues, fue_despues),
                "delta_fue": round(w_antes - w_despues, 4),
            })

    cambios.sort(key=lambda x: -x["delta_fue"])
    ahorro = round(total_antes - total_despues, 4)

    return {
        "rol": role_name,
        "fue_actual_rol": FUE_LABEL.get(current_fue, current_fue),
        "fue_nuevo_rol": FUE_LABEL.get(nuevo_fue_type, nuevo_fue_type),
        "usuarios_con_rol": len(affected_users),
        "usuarios_que_cambian_fue": len(cambios),
        "fue_total_antes": round(total_antes, 4),
        "fue_total_despues": round(total_despues, 4),
        "ahorro_fue": ahorro,
        "nota": (
            "El ahorro es sobre el FUE DERIVADO de roles. "
            "El FUE oficial facturado por SAP se actualiza al reimportar FUE_Users.xlsx."
        ),
        "detalle_cambios": cambios[:25],
    }


# ── Definición de herramientas para la API de Anthropic ─────────────────────

TOOLS_DEFINITION = [
    {
        "name": "buscar_usuarios_con_transaccion",
        "description": (
            "Busca todos los usuarios SAP que tienen acceso a una transacción específica "
            "a través de sus roles activos. Útil para preguntas como '¿qué usuarios "
            "tienen ME51N?' o '¿quién puede ejecutar FB60?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tcode": {
                    "type": "string",
                    "description": "Código de transacción SAP (ej: ME51N, FB60, MM01).",
                }
            },
            "required": ["tcode"],
        },
    },
    {
        "name": "buscar_roles_con_transaccion",
        "description": (
            "Devuelve los roles SAP que otorgan acceso a una transacción. "
            "Útil para '¿qué roles incluyen la transacción XYZ?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tcode": {"type": "string", "description": "Código de transacción SAP."}
            },
            "required": ["tcode"],
        },
    },
    {
        "name": "buscar_roles_de_usuario",
        "description": (
            "Devuelve los roles activos asignados a un usuario SAP. "
            "Útil para '¿qué roles tiene el usuario JPEREZ?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Nombre de usuario SAP (ej: JPEREZ, USR001)."}
            },
            "required": ["username"],
        },
    },
    {
        "name": "buscar_usuarios_de_rol",
        "description": (
            "Devuelve todos los usuarios que tienen asignado un rol específico. "
            "Útil para '¿quiénes tienen el rol Z_COMPRAS_APROBADOR?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role_name": {"type": "string", "description": "Nombre exacto del rol SAP."}
            },
            "required": ["role_name"],
        },
    },
    {
        "name": "obtener_conflictos_sod_usuario",
        "description": (
            "Devuelve los conflictos de Segregación de Funciones (SOD) detectados "
            "para un usuario. Útil para '¿qué conflictos tiene el usuario MLOPEZ?' "
            "o 'revisame los riesgos de JGOMEZ'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Nombre de usuario SAP."}
            },
            "required": ["username"],
        },
    },
    {
        "name": "resumen_conflictos_sod",
        "description": (
            "Devuelve estadísticas globales de conflictos SOD: total de usuarios en "
            "riesgo, distribución por nivel (crítico/alto/medio) y por módulo SAP."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "resumen_licencias",
        "description": (
            "Devuelve el resumen actual de licencias FUE: cantidad de usuarios por "
            "tipo (ADV/CORE/SELF/Sin FUE) y total de FUEs consumidos."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "simular_cambio_fue_rol",
        "description": (
            "Simula qué pasaría si se cambia la clasificación FUE de un rol. "
            "Calcula cuántos usuarios cambiarían su FUE derivado y cuántos FUEs "
            "se ahorrarían. NO modifica la base de datos — es solo una proyección. "
            "Útil para '¿cuánto ahorramos si bajamos el rol X a Self Service?' "
            "o 'si cambio el FUE del rol ZEWM_TR a CORE, ¿qué impacto tiene?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role_name": {
                    "type": "string",
                    "description": "Nombre exacto del rol SAP cuyo FUE se quiere cambiar.",
                },
                "nuevo_fue_type": {
                    "type": "string",
                    "enum": ["ADV", "CORE", "SELF", "NONE"],
                    "description": "Nuevo tipo FUE: ADV (Advanced), CORE, SELF (Self-Service) o NONE.",
                },
            },
            "required": ["role_name", "nuevo_fue_type"],
        },
    },
]

# Mapa nombre → función para el dispatcher
TOOL_FUNCTIONS = {
    "buscar_usuarios_con_transaccion": buscar_usuarios_con_transaccion,
    "buscar_roles_con_transaccion": buscar_roles_con_transaccion,
    "buscar_roles_de_usuario": buscar_roles_de_usuario,
    "buscar_usuarios_de_rol": buscar_usuarios_de_rol,
    "obtener_conflictos_sod_usuario": obtener_conflictos_sod_usuario,
    "resumen_conflictos_sod": resumen_conflictos_sod,
    "resumen_licencias": resumen_licencias,
    "simular_cambio_fue_rol": simular_cambio_fue_rol,
}
