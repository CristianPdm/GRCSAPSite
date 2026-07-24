"""Motor de analisis de Segregacion de Funciones (SOD).

Reproduce la logica de SAP_SOD_Analyzer_v640.html: cruza Rol->Transaccion
(tc2r) con Usuario->Rol (r2u) para encontrar, por cada regla activa, los
usuarios que tienen acceso a AMBOS lados del conflicto. Las excepciones
vigentes (SodException) se restan del conteo de conflictos activos.
"""
import fnmatch
import threading
from collections import defaultdict
from datetime import date

from sqlalchemy import or_

from app.sod.models import (
    SapRoleAssignment,
    SapRoleDescription,
    SapRoleTableAuth,
    SapRoleTcode,
    SapTcodeDescription,
    SapUserStatus,
    SodException,
    SodRule,
)


class _AnalysisCache:
    """Caché de módulo para los resultados del análisis SOD.

    Guarda build_maps() y build_user_risk() entre requests. Se invalida
    explícitamente cada vez que cambian los datos SAP importados (via los
    importadores) o la configuración del análisis (reglas, excepciones).

    Usa patrón double-checked locking para evitar bloquear el compute_fn
    con el mutex, eliminando el riesgo de deadlock cuando compute_fn llama
    de vuelta a build_maps() (que también usa el caché).

    Si dos threads llegan simultáneamente con caché vacío, ambos computan y
    el primero en terminar guarda el resultado; el segundo descarta el suyo.
    En un entorno GRC con pocos usuarios concurrentes, este coste puntual es
    despreciable frente a evitar la complejidad de un RLock anidado.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._maps = None       # (tc2r, r2u): plain dicts, sin objetos ORM
        self._user_risk = None  # list of plain dicts, sin objetos ORM

    def invalidate(self):
        with self._lock:
            self._maps = None
            self._user_risk = None

    def get_maps(self, compute_fn):
        if self._maps is not None:
            return self._maps
        result = compute_fn()
        with self._lock:
            if self._maps is None:
                self._maps = result
        return self._maps

    def get_user_risk(self, compute_fn):
        if self._user_risk is not None:
            return self._user_risk
        result = compute_fn()
        with self._lock:
            if self._user_risk is None:
                self._user_risk = result
        return self._user_risk


_cache = _AnalysisCache()


def invalidate_analysis_cache():
    """Invalida el caché de análisis SOD.

    Debe llamarse después de cualquier cambio en los datos SAP importados
    (AGR_USERS, AGR_1251, FUE_Rol, FUE_Users, USR02...) o en la
    configuración del análisis (reglas SOD, excepciones). Los importadores
    de app/sod/importers.py, app/licenses/importers.py y las rutas de
    gestión de reglas/excepciones en app/sod/routes.py lo llaman
    automáticamente tras cada commit.
    """
    _cache.invalidate()


def _build_children_map():
    """rol padre/compuesto -> set(roles hijos/simples), construido desde
    AGR_DEFINE (SapRoleDescription.parent_role). En SAP, un rol compuesto
    no suele tener tcodes propios en AGR_1251/AGR_TCODES: agrupa roles
    simples, y son esos roles simples los que realmente traen las
    transacciones. Sin esta expansion, un usuario con solo el rol
    compuesto asignado en AGR_USERS no aparece como en riesgo para
    ninguna regla SOD, aunque en SAP si tenga las transacciones."""
    children = defaultdict(set)
    for row in SapRoleDescription.query.filter(
        SapRoleDescription.parent_role.isnot(None),
        SapRoleDescription.parent_role != "",
    ).all():
        children[row.parent_role].add(row.role_name)
    return children


def _expand_role(role_name, children_map, _seen=None):
    """Expande un rol a si mismo + todos sus roles hijos, recursivamente
    (por si hubiera compuestos anidados). Protegido contra ciclos."""
    if _seen is None:
        _seen = set()
    if role_name in _seen:
        return set()
    _seen.add(role_name)
    expanded = {role_name}
    for child in children_map.get(role_name, set()):
        expanded |= _expand_role(child, children_map, _seen)
    return expanded


def _expand_roles(role_names, children_map):
    expanded = set()
    for role_name in role_names:
        expanded |= _expand_role(role_name, children_map)
    return expanded


def _build_maps_uncached():
    """Implementación sin caché de build_maps(). No llamar directamente."""
    tc2r = defaultdict(set)
    for row in SapRoleTcode.query.all():
        tc2r[row.tcode].add(row.role_name)

    children_map = _build_children_map()

    # Filtra asignaciones vencidas en SQL (aprovecha el índice de valid_to)
    # en lugar de hacerlo en Python después del full scan.
    today = date.today()
    r2u = defaultdict(set)
    for row in SapRoleAssignment.query.filter(
        or_(SapRoleAssignment.valid_to.is_(None), SapRoleAssignment.valid_to >= today)
    ).all():
        for effective_role in _expand_role(row.role_name, children_map):
            r2u[effective_role].add(row.username)

    return tc2r, r2u


def build_maps():
    """Construye (tc2r, r2u):
      tc2r: tcode -> set(roles) que la habilitan (AGR_1251 + AGR_TCODES, sin duplicar)
      r2u:  role -> set(usuarios) con asignacion vigente (Fecha fin vacia o futura),
            expandiendo roles compuestos a sus roles hijos (ver _build_children_map).
            Un usuario con el rol compuesto P asignado queda tambien bajo cada
            rol hijo de P en este mapa -- y P mismo sigue figurando con sus
            usuarios, para que las pantallas que listan "rol" por nombre
            literal (matriz, roles criticos) sigan mostrando P.

    El resultado se almacena en _cache (ver _AnalysisCache). Se invalida al
    importar datos nuevos o editar reglas/excepciones.
    """
    return _cache.get_maps(_build_maps_uncached)


def _users_for_tcodes(tcodes, tc2r, r2u):
    roles = set()
    for tcode in tcodes:
        roles |= tc2r.get(tcode, set())
    users = set()
    for role in roles:
        users |= r2u.get(role, set())
    return users


def run_analysis(tc2r=None, r2u=None):
    """Ejecuta el analisis sobre todas las reglas activas.

    Devuelve una lista de dicts:
      {rule: SodRule, conflicted_users: [str], excepted_users: [str], active_count: int}
    ordenada por nivel de severidad (CRITICO > ALTO > MEDIO) y luego por id.

    tc2r y r2u son opcionales: si ya fueron construidos (por build_user_risk
    u otro llamador), se pasan aquí para evitar una segunda llamada a
    build_maps(). Si no se pasan, se llama a build_maps() internamente
    (que devuelve el resultado cacheado si está disponible).
    """
    if tc2r is None or r2u is None:
        tc2r, r2u = build_maps()

    severity_order = {"CRITICO": 0, "ALTO": 1, "MEDIO": 2}
    rules = SodRule.query.filter_by(activo=True).all()

    exceptions_by_rule = defaultdict(set)
    for exc in SodException.query.all():
        exceptions_by_rule[exc.regla_id].add(exc.usuario)

    results = []
    for rule in rules:
        users1 = _users_for_tcodes(rule.tcodes1_list(), tc2r, r2u)
        users2 = _users_for_tcodes(rule.tcodes2_list(), tc2r, r2u)
        conflicted = users1 & users2

        excepted_set = exceptions_by_rule.get(rule.id, set())
        active_conflicted = sorted(conflicted - excepted_set)
        excepted_conflicted = sorted(conflicted & excepted_set)

        results.append({
            "rule": rule,
            "conflicted_users": active_conflicted,
            "excepted_users": excepted_conflicted,
            "active_count": len(active_conflicted),
        })

    results.sort(key=lambda r: (severity_order.get(r["rule"].nivel, 9), r["rule"].id))
    return results


def count_inactive_at_risk(analysis_results, inactivity_days=90):
    """Cuenta usuarios con conflicto activo cuyo ultimo acceso SAP (segun el
    modulo de Licencias, FUE_Users.xlsx) supera `inactivity_days`. Devuelve 0
    si todavia no se importaron datos de licencias -- la dependencia es
    opcional y unidireccional (SOD lee de Licencias, nunca al reves)."""
    try:
        from app.licenses.models import LicenseUser
    except ImportError:
        return 0

    last_access_by_user = {
        u.username: u.last_access for u in LicenseUser.query.all() if u.last_access
    }
    if not last_access_by_user:
        return 0

    today = date.today()
    at_risk_users = set()
    for item in analysis_results:
        for username in item["conflicted_users"]:
            last_access = last_access_by_user.get(username)
            if last_access and (today - last_access).days > inactivity_days:
                at_risk_users.add(username)

    return len(at_risk_users)


def get_sod_summary():
    """Resumen para el tile del dashboard y el encabezado de resultados.

    Nombres de campo (`critical`/`high`/`medium`/`active_users`/
    `at_risk_inactive`) elegidos para coincidir exactamente con lo que
    espera templates/main/dashboard.html."""
    has_data = SapRoleAssignment.query.first() is not None and SapRoleTcode.query.first() is not None

    # Reglas activas por modulo SAP: util para ver donde esta concentrada la
    # matriz, independientemente de si ya se importaron datos de roles/usuarios.
    rules_by_module_counts = defaultdict(int)
    for rule in SodRule.query.filter_by(activo=True).all():
        rules_by_module_counts[rule.modulo] += 1
    rules_by_module = sorted(
        ({"modulo": m, "count": c} for m, c in rules_by_module_counts.items()),
        key=lambda x: -x["count"],
    )

    if not has_data:
        return {
            "has_data": False,
            "total_rules": SodRule.query.filter_by(activo=True).count(),
            "critical": 0,
            "high": 0,
            "medium": 0,
            "active_users": 0,
            "exceptions": SodException.query.count(),
            "at_risk_inactive": 0,
            "rules_by_module": rules_by_module,
            "modulo_breakdown": [],
            "top_rules": [],
        }

    results = run_analysis()
    critical = sum(r["active_count"] for r in results if r["rule"].nivel == "CRITICO")
    high = sum(r["active_count"] for r in results if r["rule"].nivel == "ALTO")
    medium = sum(r["active_count"] for r in results if r["rule"].nivel == "MEDIO")

    active_users = set()
    for r in results:
        active_users.update(r["conflicted_users"])

    # Conflictos activos agrupados por modulo SAP (para el grafico de barras
    # "Conflictos por modulo") y el top 5 de reglas con mas usuarios en
    # conflicto (para priorizar que reglas atacar primero).
    modulo_counts = defaultdict(int)
    for r in results:
        if r["active_count"] > 0:
            modulo_counts[r["rule"].modulo] += r["active_count"]
    modulo_breakdown = sorted(
        ({"modulo": m, "count": c} for m, c in modulo_counts.items()),
        key=lambda x: -x["count"],
    )

    top_rules = sorted(
        (r for r in results if r["active_count"] > 0),
        key=lambda r: -r["active_count"],
    )[:5]
    top_rules = [
        {
            "id": r["rule"].id,
            "descripcion": r["rule"].descripcion,
            "nivel": r["rule"].nivel,
            "modulo": r["rule"].modulo,
            "active_count": r["active_count"],
        }
        for r in top_rules
    ]

    return {
        "has_data": True,
        "total_rules": len(results),
        "critical": critical,
        "high": high,
        "medium": medium,
        "active_users": len(active_users),
        "exceptions": SodException.query.count(),
        "at_risk_inactive": count_inactive_at_risk(results),
        "rules_by_module": rules_by_module,
        "modulo_breakdown": modulo_breakdown,
        "top_rules": top_rules,
    }


def _license_data():
    """Devuelve (license_users_por_usuario, license_roles_por_nombre).
    Lectura opcional y unidireccional: SOD lee de Licencias, nunca al
    reves. Si el modulo de Licencias todavia no tiene datos importados
    (o ni siquiera esta disponible), devuelve diccionarios vacios."""
    try:
        from app.licenses.models import LicenseRole, LicenseUser
    except ImportError:
        return {}, {}

    users = {u.username: u for u in LicenseUser.query.all()}
    roles = {r.role_name: r for r in LicenseRole.query.all()}
    return users, roles


def _user_status_data():
    """username -> SapUserStatus (USR02.xlsx). A diferencia de _license_data,
    SapUserStatus vive en el propio modulo SOD (no es una dependencia
    opcional de Licencias), pero igual puede no haberse importado todavia:
    devuelve un dict vacio en ese caso."""
    return {u.username: u for u in SapUserStatus.query.all()}


def _user_to_roles_map(r2u):
    """Invierte r2u (rol -> usuarios) a usuario -> set(roles)."""
    user_to_roles = defaultdict(set)
    for role, users in r2u.items():
        for username in users:
            user_to_roles[username].add(role)
    return user_to_roles


def _role_tcode_map():
    """rol -> set(tcodes) que habilita, sin importar la fuente
    (AGR_1251/AGR_TCODES, ya fusionadas en SapRoleTcode)."""
    m = defaultdict(set)
    for row in SapRoleTcode.query.all():
        m[row.role_name].add(row.tcode)
    return m


def _parent_role_names():
    """Conjunto de nombres de rol identificados como padre/compuesto: todo
    rol que aparezca como parent_role de al menos un rol hijo en AGR_DEFINE
    (ver _build_children_map). Por diseno, un rol padre no deberia tener
    asignacion propia -- ni tcodes en AGR_1251/AGR_TCODES, ni usuarios
    asignados directamente en AGR_USERS -- porque toda la autorizacion real
    la aportan sus roles hijos. Las pantallas de listado (Matriz, Roles
    criticos, Segregacion) usan este conjunto para no mostrar al rol padre
    como si fuera una asignacion mas; el calculo de riesgo SOD no se ve
    afectado, porque build_maps() ya expande la asignacion del padre a sus
    hijos antes de cruzar contra las reglas."""
    return set(_build_children_map().keys())


def get_parent_role_alerts():
    """Detecta roles padre con asignacion propia indebida (tcodes propios
    en SapRoleTcode y/o usuarios asignados directamente -no via un rol
    hijo- en AGR_USERS), para mostrar como warning en las pantallas que
    excluyen al rol padre de sus listados. Si un rol padre no tiene ninguna
    de las dos cosas (el caso esperado), no genera alerta."""
    parents = _parent_role_names()
    if not parents:
        return []

    role_tcodes = _role_tcode_map()
    role_descriptions = {d.role_name: d.description for d in SapRoleDescription.query.all()}

    today = date.today()
    direct_users = defaultdict(set)
    for row in SapRoleAssignment.query.filter(SapRoleAssignment.role_name.in_(parents)).all():
        if row.valid_to and row.valid_to < today:
            continue
        direct_users[row.role_name].add(row.username)

    alerts = []
    for rol in sorted(parents):
        tcodes = sorted(role_tcodes.get(rol, set()))
        usuarios = sorted(direct_users.get(rol, set()))
        if not tcodes and not usuarios:
            continue
        alerts.append({
            "rol": rol,
            "desc": role_descriptions.get(rol, ""),
            "tcodes": tcodes,
            "usuarios": usuarios,
            "uc": len(usuarios),
        })
    return alerts


def _matches(value, filtro):
    """Coincidencia de un filtro de texto contra un valor, insensible a
    mayusculas. Si el filtro contiene * o ?, se interpreta como patron
    wildcard (estilo SAP: * = cualquier cadena, ? = un caracter) aplicado
    al valor completo. Si no tiene wildcards, se usa busqueda por
    substring ("contiene"), igual que antes -- asi la busqueda incremental
    (letra por letra) sigue funcionando de forma intuitiva sin necesidad
    de escribir asteriscos."""
    if not filtro:
        return True
    value = value.lower()
    if "*" in filtro or "?" in filtro:
        return fnmatch.fnmatch(value, filtro)
    return filtro in value


def _matches_tcode(value, filtro):
    """Igual que _matches pero para el campo TCode: sin wildcards hace
    match exacto (case-insensitive). Con * o ? mantiene el patron wildcard.
    Ejemplo: 'me21n' solo devuelve ME21N; 'me21n*' devuelve ME21N, ME21N_CP, etc."""
    if not filtro:
        return True
    value = value.lower()
    if "*" in filtro or "?" in filtro:
        return fnmatch.fnmatch(value, filtro)
    return value == filtro


MATRIX_EXCLUDE_OPTIONS = {"", "usuario", "rol", "tcode"}
_MATRIX_EXCLUDE_FIELD = {"usuario": "user", "rol": "rol", "tcode": "tc"}


def _collapse_matrix_rows(rows, excluir):
    """Colapsa la matriz a las combinaciones unicas de las dos dimensiones
    que quedan, ocultando la dimension `excluir` ('usuario' | 'rol' |
    'tcode'). Por ejemplo, excluyendo 'rol' se ven combinaciones unicas de
    Usuario + TCode, sin una fila repetida por cada rol que se lo otorgue.
    Cada fila resultante suma en 'veces' cuantos valores distintos de la
    dimension excluida coincidian con esa combinacion (cuantos roles
    distintos, en el ejemplo). La descripcion (ligada al tcode) solo se
    conserva si el tcode no es la dimension excluida -- si se excluye
    tcode, distintas filas colapsadas pueden tener tcodes (y por lo tanto
    descripciones) distintas, asi que no se muestra ninguna para evitar
    confundir."""
    field = _MATRIX_EXCLUDE_FIELD.get(excluir)
    if not field:
        # QA: reordenar para que las filas de advertencia (roles padre con
        # asignacion propia, agregadas al final por build_matrix_rows)
        # queden intercaladas en su posicion natural en vez de al final.
        return sorted(rows, key=lambda r: (r["user"], r["rol"], r["tc"]))

    remaining = [k for k in ("user", "rol", "tc") if k != field]
    collapsed = {}
    order = []
    for row in rows:
        key = tuple(row[k] for k in remaining)
        if key not in collapsed:
            item = {k: row[k] for k in remaining}
            item["desc"] = row["desc"] if field != "tc" else ""
            item["_valores"] = set()
            # QA: preservar el flag de warning (rol padre con asignacion
            # propia) al colapsar filas -- si al menos una fila colapsada
            # es una fila de advertencia, la combinacion resultante tambien
            # se marca como advertencia.
            item["warn"] = False
            collapsed[key] = item
            order.append(key)
        collapsed[key]["_valores"].add(row[field])
        if row.get("warn"):
            collapsed[key]["warn"] = True

    result = []
    for key in order:
        item = collapsed[key]
        item["veces"] = len(item.pop("_valores"))
        result.append(item)

    result.sort(key=lambda r: tuple(r[k] for k in remaining))
    return result


def _parent_role_alert_rows(f_user, f_rol, f_tc, tcode_desc):
    """QA: filas de advertencia para roles padre con asignacion propia
    indebida (ver get_parent_role_alerts), integradas directamente en la
    Matriz en lugar de un banner aparte. Cada fila se marca con warn=True
    para que la plantilla muestre un icono junto al nombre del rol, en la
    fila del usuario afectado. Solo se consideran alertas de roles Z
    (mismo criterio que el resto de la matriz: roles hijos que empiecen
    con "Z"); en la practica los roles padre tambien suelen empezar con Z,
    pero se filtra igual por consistencia."""
    rows = []
    for a in get_parent_role_alerts():
        rol = a["rol"]
        if not rol.upper().startswith("Z"):
            continue
        if f_rol and not _matches(rol, f_rol):
            continue
        tcodes = a["tcodes"] or [""]
        for user in a["usuarios"]:
            if f_user and not _matches(user, f_user):
                continue
            for tcode in tcodes:
                if tcode:
                    if not _matches_tcode(tcode, f_tc):
                        continue
                elif f_tc:
                    continue
                rows.append({
                    "user": user,
                    "rol": rol,
                    "tc": tcode,
                    "desc": tcode_desc.get(tcode, "") if tcode else "",
                    "warn": True,
                })
    return rows


def build_matrix_rows(f_user="", f_rol="", f_tc="", excluir=""):
    """Equivalente a S.matrixRows + rMX() del original: una fila por cada
    combinacion (usuario, rol, tcode) -- el usuario tiene el rol asignado
    (activo) y el rol habilita ese tcode -- con la descripcion del tcode
    (TSTCT) si esta importada. Los filtros admiten substring simple o
    wildcards (*, ?), insensibles a mayusculas (ver _matches), aplicados
    del lado del servidor (a diferencia del original, que cargaba todas
    las filas en el navegador y filtraba ahi; con datos reales esta
    matriz puede tener decenas de miles de filas).

    `excluir` permite ademas pedir una vista "sin <dimension>": se sigue
    pudiendo filtrar por las tres (usuario/rol/tcode), pero el resultado
    se colapsa a combinaciones unicas de las dos dimensiones restantes
    (ver _collapse_matrix_rows). Solo se puede excluir una dimension a la
    vez ('usuario', 'rol' o 'tcode'; '' = sin exclusion, vista completa).

    Los roles padre/compuestos (ver _parent_role_names) no generan fila
    propia: por diseno no deberian tener tcodes propios, asi que listarlos
    aqui solo confundiria (la autorizacion real ya se ve en las filas de
    sus roles hijos). QA: si alguno tiene tcodes o usuarios propios de
    forma anomala, ahora se agrega igual como fila (marcada con warn=True)
    en lugar de solo quedar reflejado en el banner aparte de
    get_parent_role_alerts() -- ver _parent_role_alert_rows().

    QA: solo se muestran roles hijos (no compuestos) que empiecen con "Z"
    (convencion SAP para roles de cliente/custom); roles sin ese prefijo
    quedan fuera de la matriz."""
    tc2r, r2u = build_maps()
    user_to_roles = _user_to_roles_map(r2u)
    role_tcodes = _role_tcode_map()
    tcode_desc = {d.tcode: d.description for d in SapTcodeDescription.query.all()}
    parent_roles = _parent_role_names()

    f_user = (f_user or "").strip().lower()
    f_rol = (f_rol or "").strip().lower()
    f_tc = (f_tc or "").strip().lower()
    if excluir not in MATRIX_EXCLUDE_OPTIONS:
        excluir = ""

    has_wildcard = lambda s: "*" in s or "?" in s

    # ── Short-circuit: pre-filtrar candidatos antes del triple loop ──────────
    #
    # Sin pre-filtro el loop es O(usuarios × roles × tcodes): con 200 usuarios,
    # 50 roles cada uno y 300 tcodes por rol = 3M iteraciones por búsqueda.
    # Si hay filtros exactos (sin wildcards) se puede acotar el espacio:
    #
    #   f_tc exacto → tc2r[tcode] da directamente el conjunto de roles que lo
    #     tienen; solo iteramos esos roles y sus usuarios, no todos.
    #   f_user exacto → filtrar user_to_roles a un solo usuario.
    #   f_rol exacto → filtrar roles a candidatos con ese nombre exacto.

    # Pre-filtro por tcode exacto (el más selectivo: puede pasar de millones
    # de iteraciones a cientos).
    if f_tc and not has_wildcard(f_tc):
        # roles_with_tc: solo los roles que tienen exactamente este tcode.
        # BUG FIX: f_tc está en lowercase (via .lower() arriba) pero tc2r
        # usa claves en uppercase (convención SAP). Convertir a uppercase
        # para el lookup; usar la forma canónica en el output de la fila.
        f_tc_upper = f_tc.upper()
        # QA: ademas de excluir roles padre, solo se consideran roles Z
        roles_with_tc = {
            r for r in (tc2r.get(f_tc_upper, set()) - parent_roles)
            if r.upper().startswith("Z")
        }
        if not roles_with_tc:
            rows = []
        else:
            # Construir user_to_roles restringido a esos roles
            restricted: dict[str, set] = {}
            for role in roles_with_tc:
                if f_rol and not _matches(role, f_rol):
                    continue
                for username in r2u.get(role, set()):
                    if f_user and not _matches(username, f_user):
                        continue
                    restricted.setdefault(username, set()).add(role)
            # BUG FIX: sorted() sobre dicts falla en Python 3 sin key explícita
            rows = sorted(
                ({"user": u, "rol": rol, "tc": f_tc_upper, "desc": tcode_desc.get(f_tc_upper, ""), "warn": False}
                 for u, roles in restricted.items()
                 for rol in sorted(roles)),
                key=lambda r: (r["user"], r["rol"]),
            )
        # QA: agregar filas de advertencia de roles padre con asignacion
        # propia (antes un banner aparte, ahora integrado en la matriz)
        rows = rows + _parent_role_alert_rows(f_user, f_rol, f_tc, tcode_desc)
        return _collapse_matrix_rows(rows, excluir)

    # Pre-filtro por usuario exacto (sin wildcards): acota a 1 usuario.
    # BUG FIX: f_user está en lowercase pero user_to_roles tiene claves
    # en uppercase (nombres de usuario SAP). Comparar en lowercase.
    if f_user and not has_wildcard(f_user):
        candidate_users = {u: rs for u, rs in user_to_roles.items() if u.lower() == f_user}
    else:
        candidate_users = user_to_roles

    rows = []
    for username, roles in candidate_users.items():
        if not _matches(username, f_user):
            continue
        # QA: solo roles hijos (ya excluidos los padre) que empiecen con Z
        for role_name in sorted(roles - parent_roles):
            if not role_name.upper().startswith("Z"):
                continue
            if not _matches(role_name, f_rol):
                continue
            for tcode in sorted(role_tcodes.get(role_name, set())):
                if not _matches_tcode(tcode, f_tc):
                    continue
                rows.append({
                    "user": username,
                    "rol": role_name,
                    "tc": tcode,
                    "desc": tcode_desc.get(tcode, ""),
                    "warn": False,
                })

    rows.sort(key=lambda r: (r["user"], r["rol"], r["tc"]))
    # QA: agregar filas de advertencia de roles padre con asignacion propia
    # (antes un banner aparte, ahora integrado en la matriz)
    rows = rows + _parent_role_alert_rows(f_user, f_rol, f_tc, tcode_desc)
    return _collapse_matrix_rows(rows, excluir)


def _build_user_risk_uncached():
    """Implementación sin caché de build_user_risk(). No llamar directamente."""
    from app.licenses.rules import FUE_LABEL, FUE_ORDER, FUE_WEIGHT

    # build_maps() devuelve resultado cacheado si está disponible.
    # Se pasa tc2r y r2u a run_analysis() para evitar una segunda llamada
    # a build_maps() dentro de run_analysis() -- ambos aprovechan el mismo
    # resultado cacheado en la misma pasada.
    tc2r, r2u = build_maps()
    results = run_analysis(tc2r, r2u)
    user_to_roles = _user_to_roles_map(r2u)
    license_users, license_roles = _license_data()
    user_status = _user_status_data()

    severity_rank = {"CRITICO": 0, "ALTO": 1, "MEDIO": 2}
    conflicts_by_user = defaultdict(list)
    niveles_by_user = defaultdict(set)
    for item in results:
        rule = item["rule"]
        for username in item["conflicted_users"]:
            conflicts_by_user[username].append(rule.id)
            niveles_by_user[username].add(rule.nivel)

    today = date.today()
    rows = []
    for username, roles in user_to_roles.items():
        role_fue_order = 0
        role_fue_code = "NONE"
        fue_roles = []
        for role_name in sorted(roles):
            lic_role = license_roles.get(role_name)
            code = lic_role.fue_type_code if lic_role else "NONE"
            if lic_role and code != "NONE":
                fue_roles.append({"rol": role_name, "type": code, "label": FUE_LABEL[code]})
            if FUE_ORDER.get(code, 0) > role_fue_order:
                role_fue_order = FUE_ORDER.get(code, 0)
                role_fue_code = code

        niveles = niveles_by_user.get(username, set())
        if "CRITICO" in niveles:
            mx = "CRITICO"
        elif "ALTO" in niveles:
            mx = "ALTO"
        elif niveles:
            mx = "MEDIO"
        else:
            mx = "SIN"

        lic_user = license_users.get(username)
        last_access = lic_user.last_access if lic_user else None
        days = (today - last_access).days if last_access else None

        status = user_status.get(username)

        rows.append({
            "user": username,
            "nombre": lic_user.full_name if lic_user else "",
            "role_count": len(roles),
            "cc": len(conflicts_by_user.get(username, [])),
            "conflicts": conflicts_by_user.get(username, []),
            "mx": mx,
            "fue_type": role_fue_code,
            "fue_label": FUE_LABEL[role_fue_code],
            "fue_weight": FUE_WEIGHT[role_fue_code],
            "fue_roles": fue_roles,
            "fue_from_db_code": lic_user.fue_type_code if lic_user else None,
            "fue_from_db_label": FUE_LABEL.get(lic_user.fue_type_code, "Sin tipo FUE") if lic_user else None,
            "indice_fue": lic_user.indice_fue if lic_user else "",
            "last_access": last_access,
            "days": days,
            "has_license_data": lic_user is not None,
            "bloqueado": status.bloqueado if status else None,
            "lock_label": status.lock_label if status else None,
            "ultimo_login_sap": status.ultimo_login if status else None,
            "has_user_status": status is not None,
        })

    rows.sort(key=lambda r: (severity_rank.get(r["mx"], 9), -r["cc"], r["user"]))
    return rows


def build_user_risk():
    """Equivalente a S.userRisk del original: un registro por usuario con
    al menos un rol activo, cruzando sus conflictos SOD con el FUE
    derivado de sus roles y (si hay datos importados) el FUE oficial SAP
    y la inactividad, leidos del modulo de Licencias.

    El resultado se almacena en _cache. Se invalida al importar datos nuevos
    o editar reglas/excepciones (ver invalidate_analysis_cache).
    """
    return _cache.get_user_risk(_build_user_risk_uncached)


def build_role_crit():
    """Equivalente a S.roleCrit del original: un registro por rol
    involucrado en al menos un conflicto SOD (roles que habilitan ambos
    lados de alguna regla activa) o con la bandera S_TABU_DIS=*, con su
    FUE de referencia, usuarios asignados y tcodes.

    Excluye a los roles padre/compuestos (ver _parent_role_names): no
    deberian tener tcodes ni usuarios propios, asi que no se listan como
    "rol critico" por su nombre literal -- el riesgo real de sus roles
    hijos ya aparece en las filas de esos hijos. Si un rol padre tiene
    asignacion propia de forma anomala, queda reflejado en
    get_parent_role_alerts()."""
    from app.licenses.rules import FUE_LABEL

    tc2r, r2u = build_maps()
    rules = SodRule.query.filter_by(activo=True).all()
    role_tcodes = _role_tcode_map()
    role_descriptions = {d.role_name: d.description for d in SapRoleDescription.query.all()}
    tabu_dis_roles = {row.role_name for row in SapRoleTableAuth.query.all()}
    parent_roles = _parent_role_names()
    _, license_roles = _license_data()

    rcm = defaultdict(lambda: {"ids": [], "niveles": set()})
    for rule in rules:
        roles1, roles2 = set(), set()
        for tcode in rule.tcodes1_list():
            roles1 |= tc2r.get(tcode, set())
        for tcode in rule.tcodes2_list():
            roles2 |= tc2r.get(tcode, set())
        if not roles1 or not roles2:
            continue
        for role_name in roles1 & roles2:
            rcm[role_name]["ids"].append(rule.id)
            rcm[role_name]["niveles"].add(rule.nivel)

    for role_name in tabu_dis_roles:
        rcm[role_name]["ids"].append("S_TABU_DIS=*")
        rcm[role_name]["niveles"].add("ALTO")

    severity_rank = {"CRITICO": 0, "ALTO": 1, "MEDIO": 2}
    rows = []
    for role_name in (set(r2u.keys()) | set(rcm.keys())) - parent_roles:
        info = rcm.get(role_name)
        niveles = info["niveles"] if info else set()
        if "CRITICO" in niveles:
            mx = "CRITICO"
        elif "ALTO" in niveles:
            mx = "ALTO"
        elif niveles:
            mx = "MEDIO"
        else:
            mx = "SIN"

        lic_role = license_roles.get(role_name)
        role_tcs = sorted(role_tcodes.get(role_name, set()))
        rows.append({
            "rol": role_name,
            "mx": mx,
            "ids": info["ids"] if info else [],
            "tabu_dis": role_name in tabu_dis_roles,
            "uc": len(r2u.get(role_name, set())),
            "users": sorted(r2u.get(role_name, set())),
            "tcs": role_tcs[:10],
            "tcs_total": len(role_tcs),
            "desc": role_descriptions.get(role_name, "") or (lic_role.description if lic_role else ""),
            "fue_code": lic_role.fue_type_code if lic_role else None,
            "fue_label": FUE_LABEL.get(lic_role.fue_type_code) if lic_role else None,
            "fue_ratio": lic_role.ratio if lic_role else "",
        })

    rows.sort(key=lambda r: (severity_rank.get(r["mx"], 9), -r["uc"], r["rol"]))
    return rows


def validate_new_role(username, new_role_names):
    """Equivalente a runVal() del original: simula el efecto de asignarle a
    `username` los roles en `new_role_names`, ademas de los que ya tiene
    activos, ANTES de ejecutar la asignacion real en SAP.

    Diferencia deliberada con el original: los roles "actuales" del usuario
    se toman de r2u (solo asignaciones activas, via build_maps()), en vez
    de todas las filas crudas de AGR_USERS sin filtrar por fecha de
    vencimiento -- es la opcion consistente con el resto del motor
    (build_user_risk()/build_role_crit()).

    Un conflicto se cuenta como "nuevo" si la regla queda cubierta por
    ambos lados (como en run_analysis()) Y al menos uno de esos lados solo
    queda cubierto gracias a un tcode que aportan los roles nuevos (no lo
    tenia ya por sus roles actuales). Asi se detectan tanto conflictos
    ineditos como conflictos ya existentes que los roles nuevos refuerzan.
    """
    from app.licenses.rules import FUE_LABEL, FUE_ORDER

    tc2r, r2u = build_maps()
    role_tcodes = _role_tcode_map()
    children_map = _build_children_map()
    _, license_roles = _license_data()
    role_descriptions = {d.role_name: d.description for d in SapRoleDescription.query.all()}
    known_roles = set(role_tcodes.keys()) | set(r2u.keys())

    username = (username or "").strip().upper()
    new_roles = sorted({r.strip() for r in new_role_names if r and r.strip()})

    current_roles = sorted(role for role, users in r2u.items() if username in users)

    tc_curr = set()
    for role in current_roles:
        tc_curr |= role_tcodes.get(role, set())
    tc_all_new = set()
    for role in _expand_roles(new_roles, children_map):
        tc_all_new |= role_tcodes.get(role, set())
    tc_neto = tc_all_new - tc_curr
    tc_total = tc_curr | tc_all_new

    new_conflicts = []
    rules = SodRule.query.filter_by(activo=True).all()
    for rule in rules:
        t1 = rule.tcodes1_list()
        t2 = rule.tcodes2_list()
        has_a = any(tc in tc_total for tc in t1)
        has_b = any(tc in tc_total for tc in t2)
        net_a = any(tc in tc_neto for tc in t1)
        net_b = any(tc in tc_neto for tc in t2)
        if has_a and has_b and (net_a or net_b):
            sides = t1 + t2
            contributions = []
            for role in new_roles:
                rtc = set()
                for effective_role in _expand_role(role, children_map):
                    rtc |= role_tcodes.get(effective_role, set())
                aporte = sorted({tc for tc in sides if tc in rtc and tc not in tc_curr})
                if aporte:
                    contributions.append({"rol": role, "tcodes": aporte})
            new_conflicts.append({"rule": rule, "from_new_roles": contributions})

    def role_fue_code(role_name):
        lic = license_roles.get(role_name)
        return lic.fue_type_code if lic else "NONE"

    cur_fue_order, cur_fue_code = 0, "NONE"
    for role in current_roles:
        code = role_fue_code(role)
        if FUE_ORDER.get(code, 0) > cur_fue_order:
            cur_fue_order, cur_fue_code = FUE_ORDER.get(code, 0), code

    result_fue_order, result_fue_code = cur_fue_order, cur_fue_code
    new_roles_info = []
    for role in new_roles:
        code = role_fue_code(role)
        if FUE_ORDER.get(code, 0) > result_fue_order:
            result_fue_order, result_fue_code = FUE_ORDER.get(code, 0), code
        new_roles_info.append({
            "rol": role,
            "desc": role_descriptions.get(role, ""),
            "fue_code": code,
            "fue_label": FUE_LABEL.get(code, "Sin tipo FUE"),
            "known": role in known_roles,
        })

    upgrade = result_fue_order > cur_fue_order

    niveles = {item["rule"].nivel for item in new_conflicts}
    if "CRITICO" in niveles or "ALTO" in niveles:
        decision = "RECHAZAR"
    elif "MEDIO" in niveles:
        decision = "EXCEPCION_DOCUMENTADA"
    elif upgrade:
        decision = "REVISAR_FUE"
    else:
        decision = "APROBAR"

    return {
        "username": username,
        "current_roles": current_roles,
        "new_roles": new_roles,
        "new_roles_info": new_roles_info,
        "new_conflicts": new_conflicts,
        "tc_neto_count": len(tc_neto),
        "tc_total_count": len(tc_total),
        "current_fue_code": cur_fue_code,
        "current_fue_label": FUE_LABEL.get(cur_fue_code, "Sin tipo FUE"),
        "result_fue_code": result_fue_code,
        "result_fue_label": FUE_LABEL.get(result_fue_code, "Sin tipo FUE"),
        "upgrade": upgrade,
        "decision": decision,
    }


def build_role_segregation_proposals():
    """Detecta roles "en riesgo" en el sentido mas grave posible: el propio
    rol habilita transacciones de AMBOS lados de una regla SOD activa (no
    por combinarse con otro rol que tenga el usuario, sino por su propio
    diseno). A esto se le llama autoconflicto -- ningun movimiento de
    usuarios entre roles lo resuelve, porque cualquier persona a la que se
    le asigne ese rol queda en conflicto de inmediato. La unica correccion
    posible es modificar el rol: separarlo en dos.

    Para cada rol con autoconflicto, propone una segregacion concreta:
    que tcodes quedarian en el rol original y cuales se moverian a un rol
    nuevo, junto con los usuarios actualmente asignados (que habria que
    revisar uno por uno tras dividir el rol, porque algunos necesitaran
    los dos roles resultantes y otros solo uno).

    Si el rol viola mas de una regla activa, la propuesta de division se
    basa en la regla mas severa (CRITICO > ALTO > MEDIO); las demas quedan
    listadas en 'conflictos' para revisar despues de aplicar el primer
    split (dividir por la regla principal no garantiza resolver las
    otras si involucran un agrupamiento distinto de tcodes).

    Excluye a los roles padre/compuestos (ver _parent_role_names): por
    diseno no deberian tener tcodes propios, asi que no entran en este
    analisis de autoconflicto por nombre literal. Si alguno tiene tcodes
    propios de forma anomala (incluyendo un autoconflicto), queda
    reflejado en get_parent_role_alerts() en lugar de aqui."""
    _, r2u = build_maps()
    role_tcodes = _role_tcode_map()
    role_descriptions = {d.role_name: d.description for d in SapRoleDescription.query.all()}
    rules = SodRule.query.filter_by(activo=True).all()
    parent_roles = _parent_role_names()
    severity_rank = {"CRITICO": 0, "ALTO": 1, "MEDIO": 2}

    by_role = defaultdict(list)
    for rule in rules:
        t1 = set(rule.tcodes1_list())
        t2 = set(rule.tcodes2_list())
        if not t1 or not t2:
            continue
        for role_name, tcs in role_tcodes.items():
            if role_name in parent_roles:
                continue
            lado_a = sorted(tcs & t1)
            lado_b = sorted(tcs & t2)
            if lado_a and lado_b:
                by_role[role_name].append({
                    "rule": rule,
                    "lado_a_label": rule.permiso1 or "Lado A",
                    "lado_a_tcodes": lado_a,
                    "lado_b_label": rule.permiso2 or "Lado B",
                    "lado_b_tcodes": lado_b,
                })

    rows = []
    for role_name, conflictos in by_role.items():
        conflictos.sort(key=lambda c: severity_rank.get(c["rule"].nivel, 9))
        niveles = {c["rule"].nivel for c in conflictos}
        if "CRITICO" in niveles:
            mx = "CRITICO"
        elif "ALTO" in niveles:
            mx = "ALTO"
        else:
            mx = "MEDIO"

        # Regla principal = la mas severa (conflictos ya viene ordenado).
        # Para decidir la division, se extrae del rol el lado con MENOS
        # tcodes (cambio minimo) y se deja el lado mayor en el rol original.
        principal = conflictos[0]
        lado_a_tc = set(principal["lado_a_tcodes"])
        lado_b_tc = set(principal["lado_b_tcodes"])
        if len(lado_b_tc) <= len(lado_a_tc):
            tcodes_mantener, label_mantener = lado_a_tc, principal["lado_a_label"]
            tcodes_extraer, label_extraer = lado_b_tc, principal["lado_b_label"]
        else:
            tcodes_mantener, label_mantener = lado_b_tc, principal["lado_b_label"]
            tcodes_extraer, label_extraer = lado_a_tc, principal["lado_a_label"]

        usuarios = sorted(r2u.get(role_name, set()))
        rows.append({
            "rol": role_name,
            "desc": role_descriptions.get(role_name, ""),
            "mx": mx,
            "usuarios": usuarios,
            "uc": len(usuarios),
            "conflictos": conflictos,
            "cc": len(conflictos),
            "tiene_multiples_reglas": len(conflictos) > 1,
            "label_mantener": label_mantener,
            "tcodes_mantener": sorted(tcodes_mantener),
            "label_extraer": label_extraer,
            "tcodes_extraer": sorted(tcodes_extraer),
            "rol_nuevo_sugerido": f"{role_name}_SOD",
        })

    rows.sort(key=lambda r: (severity_rank.get(r["mx"], 9), -r["cc"], -r["uc"], r["rol"]))
    return rows


def build_audit_report_data():
    """Datos para el Informe de Auditoria GRC-SOD (equivalente a expReport()
    de SAP_SOD_Analyzer_v640.html). Reutiliza get_sod_summary(), run_analysis(),
    build_user_risk() y build_role_crit() -- no recalcula nada que ya exista.

    El desglose FUE (fue_stats/fue_top_rows) se toma del resumen oficial del
    modulo de Licencias (FUE_Users.xlsx, via get_license_summary()) cuando
    hay datos importados; si no, se deriva de build_user_risk() (FUE segun
    roles asignados), igual que hacia el original cuando no tenia S.fueMeta.
    """
    from app.licenses.engine import get_license_summary

    summary = get_sod_summary()
    results = run_analysis()
    user_risk = build_user_risk()
    role_crit = build_role_crit()

    _, r2u = build_maps()
    total_usuarios_activos = len({u for users in r2u.values() for u in users})
    total_roles_en_uso = len(r2u)

    license_summary = get_license_summary()
    fue_source_oficial = license_summary["has_data"]
    if fue_source_oficial:
        from app.licenses.rules import FUE_WEIGHT

        fue_stats = {
            "ADV": license_summary["advanced"],
            "CORE": license_summary["core"],
            "SELF": license_summary["self_service"],
            "total_fue": license_summary["total_fue"],
        }
        fue_top_rows = [
            {
                "user": u["user"],
                "nombre": u["nombre"],
                "fue_code": u["fue_from_db_code"],
                "fue_label": u["fue_from_db_label"],
                "fue_weight": FUE_WEIGHT.get(u["fue_from_db_code"], 0.0),
                "last_access": u["last_access"],
            }
            for u in user_risk
            if u["fue_from_db_code"] in ("ADV", "CORE", "SELF")
        ]
    else:
        adv = sum(1 for u in user_risk if u["fue_type"] == "ADV")
        core = sum(1 for u in user_risk if u["fue_type"] == "CORE")
        selfsvc = sum(1 for u in user_risk if u["fue_type"] == "SELF")
        fue_stats = {
            "ADV": adv,
            "CORE": core,
            "SELF": selfsvc,
            "total_fue": round(sum(u["fue_weight"] for u in user_risk), 2),
        }
        fue_top_rows = [
            {
                "user": u["user"],
                "fue_code": u["fue_type"],
                "fue_label": u["fue_label"],
                "fue_weight": u["fue_weight"],
                "fue_roles": u["fue_roles"],
            }
            for u in user_risk
            if u["fue_type"] != "NONE"
        ]
    fue_top_rows.sort(key=lambda r: -r["fue_weight"])
    fue_top_rows = fue_top_rows[:60]

    # "roles involucrados" por regla: los mismos roles que build_role_crit()
    # ya identifica como causantes de cada conflicto (rcm[role]["ids"]),
    # invertidos a regla -> [roles].
    roles_by_rule_id = defaultdict(list)
    for r in role_crit:
        for rule_id in r["ids"]:
            roles_by_rule_id[rule_id].append(r["rol"])

    rules_with_conflict = [r for r in results if r["conflicted_users"] or r["excepted_users"]]
    rules_active = [r for r in rules_with_conflict if r["active_count"] > 0]
    rules_fully_excepted = [r for r in rules_with_conflict if r["active_count"] == 0]

    criticos_activos = [r for r in rules_active if r["rule"].nivel == "CRITICO"]
    altos_activos = [r for r in rules_active if r["rule"].nivel == "ALTO"]
    medios_activos = [r for r in rules_active if r["rule"].nivel == "MEDIO"]

    modulos = sorted({r["rule"].modulo for r in rules_active})
    modulo_breakdown = []
    for modulo in modulos:
        items = [r for r in rules_active if r["rule"].modulo == modulo]
        modulo_breakdown.append({
            "modulo": modulo,
            "critico": sum(1 for r in items if r["rule"].nivel == "CRITICO"),
            "alto": sum(1 for r in items if r["rule"].nivel == "ALTO"),
            "medio": sum(1 for r in items if r["rule"].nivel == "MEDIO"),
            "total": len(items),
        })

    users_en_riesgo_all = [u for u in user_risk if u["cc"] > 0]
    roles_criticos_count = sum(1 for r in role_crit if r["mx"] == "CRITICO")

    hits_by_rule = {
        r["rule"].id: len(r["conflicted_users"]) + len(r["excepted_users"])
        for r in results
    }
    todas_las_reglas = SodRule.query.order_by(SodRule.id).all()

    return {
        "summary": summary,
        "fue_stats": fue_stats,
        "fue_source_oficial": fue_source_oficial,
        "fue_top_rows": fue_top_rows,
        "total_usuarios_activos": total_usuarios_activos,
        "total_roles_en_uso": total_roles_en_uso,
        "users_en_riesgo": users_en_riesgo_all[:50],
        "users_en_riesgo_total": len(users_en_riesgo_all),
        "roles_criticos": role_crit[:60],
        "roles_criticos_total": len(role_crit),
        "roles_criticos_count": roles_criticos_count,
        "modulo_breakdown": modulo_breakdown,
        "criticos_activos": criticos_activos,
        "altos_activos": altos_activos,
        "medios_activos": medios_activos,
        "rules_active_count": len(rules_active),
        "rules_fully_excepted_count": len(rules_fully_excepted),
        "roles_by_rule_id": dict(roles_by_rule_id),
        "todas_las_reglas": todas_las_reglas,
        "hits_by_rule": hits_by_rule,
        "total_rules_count": SodRule.query.count(),
    }
