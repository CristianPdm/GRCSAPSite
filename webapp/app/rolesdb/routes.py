"""Módulo de análisis de Roles y Transacciones.

Permite consultar:
  - Qué transacciones/apps tiene un rol (clásicas y Fiori).
  - En qué roles aparece una transacción/app determinada.
  - Información de roles compuestos (padre ↔ hijos).
"""
import re

from flask import render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required

from app.decorators import permission_required
from app.rolesdb import bp
from app.extensions import db
from app.sod.models import (
    SapRoleTcode,
    SapRoleDescription,
    SapRoleHierNode,
    SapBuffiUrl,
    SapFioriIdTcode,
    SapFioriAppReg,
    SapTcodeDescription,
    SapRoleAssignment,
    SapRoleOrgLevel,
    SapOrgLevelDesc,
)
# QA 6.a: importar LicenseRole para mostrar FUE de cada rol
# QA: importar LicenseUser para mostrar FUE de cada usuario asignado
from app.licenses.models import LicenseRole, LicenseUser

# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

# Patrón para extraer el ID técnico del catálogo/grupo Fiori desde una URL
# de AGR_BUFFI con formato "...X-SAP-UI2-CATALOG:<ID>..." o variantes.
_CATALOG_PAT = re.compile(r"X-SAP-UI2-[^:]+:([^?&\s\"']+)", re.IGNORECASE)


def _active_filter(query, model):
    """Filtra asignaciones de rol vigentes (valid_to nulo o a futuro)."""
    from datetime import date
    return query.filter(
        (model.valid_to == None) | (model.valid_to >= date.today())  # noqa: E711
    )


def _all_fiori_tcodes():
    """Conjunto de tcodes Fiori del sistema.

    Union de dos fuentes:
      1. SapFioriIdTcode: tcodes resueltos via PB_C_CHIPM + recompute.
      2. SapFioriAppReg: mapeo directo de SUI_TM_MM_APP (cubre apps OData
         como F0805A donde app_id != component_id del chip, que no pasan
         por la resolucion de chips hasta el proximo reimport).
    """
    tcodes = {r.tcode for r in SapFioriIdTcode.query.with_entities(SapFioriIdTcode.tcode).distinct().all()}
    tcodes |= {r.tcode for r in SapFioriAppReg.query.with_entities(SapFioriAppReg.tcode).distinct().all()}
    return tcodes


def _tcode_desc_map(tcodes):
    """Devuelve dict tcode → descripción TSTCT para un conjunto de tcodes."""
    if not tcodes:
        return {}
    rows = SapTcodeDescription.query.filter(
        SapTcodeDescription.tcode.in_(tcodes)
    ).all()
    return {r.tcode: r.description for r in rows}


def _fiori_tcode_titles(tcodes):
    """Devuelve dict tcode → titulo_fiori para un conjunto de tcodes.
    Usa el primer titulo no vacío encontrado en SapFioriIdTcode."""
    if not tcodes:
        return {}
    rows = SapFioriIdTcode.query.filter(
        SapFioriIdTcode.tcode.in_(tcodes)
    ).all()
    result = {}
    for r in rows:
        if r.tcode not in result or (not result[r.tcode] and r.titulo):
            result[r.tcode] = r.titulo or ""
    return result


def _get_role_tcodes(role_name):
    """Devuelve lista de dicts con todas las transacciones de un rol.

    Combina:
      1. Tcodes directos de AGR_1251 (SapRoleTcode).
      2. Tcodes resueltos desde catálogos/grupos Fiori del rol
         (SapRoleHierNode → SapBuffiUrl → SapFioriIdTcode).

    Cada elemento del resultado:
        tcode       : str
        descripcion : str  (TSTCT)
        es_fiori    : bool
        titulo_fiori: str  (display_title_text del chip)
        fuente      : "AGR_1251" | "Fiori" | "AGR_1251+Fiori"
    """
    # 1. Tcodes directos
    direct_tcodes = {
        r.tcode
        for r in SapRoleTcode.query.filter_by(role_name=role_name).all()
    }

    # 2. Tcodes via catálogos Fiori del rol
    hier_nodes = SapRoleHierNode.query.filter_by(role_name=role_name).all()
    fiori_tcode_titles_local = {}  # tcode → titulo

    if hier_nodes:
        counters = {n.contador for n in hier_nodes}
        buffi_rows = SapBuffiUrl.query.filter(
            SapBuffiUrl.role_name == role_name,
            SapBuffiUrl.contador.in_(counters),
        ).all()

        # Extraer IDs de catálogos/grupos desde las URLs
        cat_ids = set()
        for b in buffi_rows:
            m = _CATALOG_PAT.search(b.url or "")
            if m:
                cat_ids.add(m.group(1))

        if cat_ids:
            fiori_rows = SapFioriIdTcode.query.filter(
                SapFioriIdTcode.catalog_or_group_id.in_(cat_ids)
            ).all()
            for f in fiori_rows:
                # Prioriza el primer titulo no vacío
                if f.tcode not in fiori_tcode_titles_local or (
                    not fiori_tcode_titles_local[f.tcode] and f.titulo
                ):
                    fiori_tcode_titles_local[f.tcode] = f.titulo or ""

    fiori_tcodes_local = set(fiori_tcode_titles_local.keys())

    # 3. Para marcar como Fiori los tcodes de AGR_1251 que también existen
    #    en algún catálogo del sistema (no necesariamente de este rol)
    all_fiori = _all_fiori_tcodes()

    # 4. Unión de tcodes
    all_tcodes = direct_tcodes | fiori_tcodes_local

    # 5. Descripciones TSTCT y titulos Fiori globales
    desc_map = _tcode_desc_map(all_tcodes)
    global_titles = _fiori_tcode_titles(all_tcodes)

    # 6. Construir resultado
    result = []
    for tcode in all_tcodes:
        in_direct = tcode in direct_tcodes
        in_fiori_local = tcode in fiori_tcodes_local

        # Fiori SOLO si el propio rol lo tiene via catalogo Fiori.
        # Usar all_fiori (global) marcaria como Fiori tcodes clasicos que
        # aparecen en algun catalogo de otro rol pero se usan clasicamente.
        es_fiori = in_fiori_local

        titulo = fiori_tcode_titles_local.get(tcode) or global_titles.get(tcode, "")

        result.append({
            "tcode": tcode,
            "descripcion": desc_map.get(tcode, ""),
            "es_fiori": es_fiori,
            "titulo_fiori": titulo,
        })

    result.sort(key=lambda x: x["tcode"])
    return result


def _table_exists(table_name):
    """Comprueba si una tabla existe en la BD (para migraciones aún no
    aplicadas, ej. sap_role_org_levels antes de correr
    migrate_add_org_levels.py)."""
    from sqlalchemy import inspect
    insp = inspect(db.engine)
    return table_name in insp.get_table_names()


def _get_role_org_levels(role_name):
    """Devuelve los niveles organizacionales (AGR_1252.xlsx) de un rol, a
    nivel cabecera: a qué áreas de la organización (centro, sociedad,
    org. de compras, etc.) da acceso el rol, además de sus transacciones.

    Agrupa por nivel organizacional (`nivel_codigo`, ej. '$WERKS'): un
    mismo nivel puede tener varios valores autorizados (varias filas en
    AGR_1252), y el valor puede venir vacío -- esas filas sin valor se
    omiten porque no aportan información para mostrar.

    Cada elemento del resultado:
        codigo      : str  (ej. '$WERKS')
        descripcion : str  (USVAR.xlsx, si está importado; si no, vacío)
        valores     : list[str]  (uno por fila con valor; un rango
                                   valor_bajo/valor_alto se muestra como
                                   "bajo - alto")
    """
    if not _table_exists("sap_role_org_levels"):
        return []

    rows = SapRoleOrgLevel.query.filter_by(role_name=role_name).all()
    if not rows:
        return []

    desc_map = {}
    if _table_exists("sap_org_level_descriptions"):
        codigos = {r.nivel_codigo for r in rows}
        desc_map = {
            d.codigo: d.descripcion
            for d in SapOrgLevelDesc.query.filter(SapOrgLevelDesc.codigo.in_(codigos)).all()
        }

    valores_por_nivel = {}
    orden = []
    for r in rows:
        bajo = (r.valor_bajo or "").strip()
        alto = (r.valor_alto or "").strip()
        if not bajo and not alto:
            continue  # nivel definido en el rol pero sin valor cargado
        valor = f"{bajo} - {alto}" if bajo and alto else (bajo or alto)
        if r.nivel_codigo not in valores_por_nivel:
            valores_por_nivel[r.nivel_codigo] = []
            orden.append(r.nivel_codigo)
        if valor not in valores_por_nivel[r.nivel_codigo]:
            valores_por_nivel[r.nivel_codigo].append(valor)

    return [
        {
            "codigo": codigo,
            "descripcion": desc_map.get(codigo, ""),
            "valores": sorted(valores_por_nivel[codigo]),
        }
        for codigo in sorted(orden)
    ]


def _get_role_info(role_name):
    """Devuelve dict con descripción, parent, hijos y usuarios del rol."""
    desc_row = SapRoleDescription.query.filter_by(role_name=role_name).first()
    description = desc_row.description if desc_row else ""
    parent = desc_row.parent_role if desc_row else None

    # Roles hijos (si este es un rol compuesto)
    children_rows = SapRoleDescription.query.filter_by(parent_role=role_name).all()
    children = [{"role_name": c.role_name, "description": c.description} for c in children_rows]

    # Usuarios vigentes asignados a este rol
    users_q = _active_filter(
        SapRoleAssignment.query.filter_by(role_name=role_name),
        SapRoleAssignment,
    )
    users = [r.username for r in users_q.order_by(SapRoleAssignment.username).all()]

    # QA: nombre completo y tipo de FUE de cada usuario (FUE_Users.xlsx),
    # para mostrar junto al usuario en el listado de "Usuarios asignados"
    fue_by_user = {
        u.username: u
        for u in LicenseUser.query.filter(LicenseUser.username.in_(users)).all()
    } if users else {}
    users_detail = [
        {
            "username": username,
            "full_name": fue_by_user[username].full_name if username in fue_by_user else "",
            "fue_code": fue_by_user[username].fue_type_code if username in fue_by_user else None,
            "fue_label": fue_by_user[username].fue_type_raw if username in fue_by_user else None,
        }
        for username in users
    ]

    return {
        "role_name": role_name,
        "description": description,
        "parent": parent,
        "is_composite": len(children) > 0,
        "children": children,
        "users_detail": users_detail,
        "users": users,
        "user_count": len(users),
    }


def _get_roles_with_tcode(tcode):
    """Devuelve lista de roles que contienen el tcode dado.

    Busca en:
      1. SapRoleTcode (AGR_1251).
      2. SapFioriIdTcode → SapBuffiUrl → SapRoleHierNode (catálogos Fiori).
    """
    # 1. Roles directos de AGR_1251
    direct_roles = {
        r.role_name
        for r in SapRoleTcode.query.filter_by(tcode=tcode).all()
    }

    # 2. Roles via catálogos Fiori que incluyen este tcode
    fiori_cat_rows = SapFioriIdTcode.query.filter_by(tcode=tcode).all()
    cat_ids = {r.catalog_or_group_id for r in fiori_cat_rows}

    fiori_roles = set()
    if cat_ids:
        # Obtener todas las URLs que contienen alguno de esos cat_ids
        all_buffi = SapBuffiUrl.query.all()
        matched_pairs = set()  # (role_name, contador)
        for b in all_buffi:
            m = _CATALOG_PAT.search(b.url or "")
            if m and m.group(1) in cat_ids:
                matched_pairs.add((b.role_name, b.contador))

        if matched_pairs:
            # Verificar que esas (role_name, contador) existan en SapRoleHierNode
            for role_name, contador in matched_pairs:
                node = SapRoleHierNode.query.filter_by(
                    role_name=role_name, contador=contador
                ).first()
                if node:
                    fiori_roles.add(role_name)

    all_roles = direct_roles | fiori_roles

    # Incluir roles padres (compuestos) de los roles encontrados.
    # Ej: si ZSD_VENTAS tiene F0797, también mostrar Z_COMP_VENTAS.
    if all_roles:
        parent_roles = {
            r.parent_role
            for r in SapRoleDescription.query.filter(
                SapRoleDescription.role_name.in_(all_roles),
                SapRoleDescription.parent_role.isnot(None),
            ).all()
            if r.parent_role
        }
        all_roles = all_roles | parent_roles

    # Información adicional por rol
    if not all_roles:
        return []

    desc_map = {
        r.role_name: r
        for r in SapRoleDescription.query.filter(
            SapRoleDescription.role_name.in_(all_roles)
        ).all()
    }

    # Usuarios por rol (conteo)
    user_counts = {}
    for role in all_roles:
        q = _active_filter(
            SapRoleAssignment.query.filter_by(role_name=role),
            SapRoleAssignment,
        )
        user_counts[role] = q.count()

    # Detectar si el rol es compuesto (tiene hijos)
    composites = {
        r.parent_role
        for r in SapRoleDescription.query.filter(
            SapRoleDescription.parent_role.in_(all_roles)
        ).all()
        if r.parent_role
    }

    # QA: tipo de FUE de cada rol (FUE_Rol.xlsx), para mostrar junto a la
    # descripcion en la tabla de "roles con esta transaccion".
    fue_map = {
        r.role_name: r
        for r in LicenseRole.query.filter(LicenseRole.role_name.in_(all_roles)).all()
    }

    result = []
    for role in all_roles:
        d = desc_map.get(role)
        fue_row = fue_map.get(role)
        result.append({
            "role_name": role,
            "description": d.description if d else "",
            "parent": d.parent_role if d else None,
            "is_composite": role in composites,
            "user_count": user_counts.get(role, 0),
            "via_fiori": role in fiori_roles,
            "via_agr1251": role in direct_roles,
            "fue_code": fue_row.fue_type_code if fue_row else None,
            "fue_label": fue_row.fue_type_raw if fue_row else None,
        })

    result.sort(key=lambda x: x["role_name"])
    return result


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

@bp.route("/")
@login_required
@permission_required("can_view_rolesdb")
def index():
    """Página principal: formularios de búsqueda por rol y por transacción."""
    return render_template("rolesdb/index.html")


@bp.route("/rol/<path:role_name>")
@login_required
@permission_required("can_view_rolesdb")
def rol_detail(role_name):
    """Detalle de un rol: sus transacciones, tipo (Fiori/Clásica) y usuarios.
    QA: solo se permite consultar roles hijos (no compuestos/padre) que
    ademas empiecen con "Z" (convencion SAP para roles de cliente/custom)."""
    es_padre = SapRoleDescription.query.filter_by(parent_role=role_name).first() is not None
    if not role_name.upper().startswith("Z") or es_padre:
        flash('Solo se pueden consultar roles hijos que empiecen con "Z".', "error")
        return redirect(url_for("rolesdb.index"))

    info = _get_role_info(role_name)
    tcodes = _get_role_tcodes(role_name)

    # Si es un rol compuesto, también agrega los tcodes de los hijos
    child_tcodes = {}  # tcode → lista de roles hijos que lo traen
    if info["is_composite"]:
        for child in info["children"]:
            for t in _get_role_tcodes(child["role_name"]):
                child_tcodes.setdefault(t["tcode"], []).append(child["role_name"])

    fiori_count = sum(1 for t in tcodes if t["es_fiori"])
    clasica_count = len(tcodes) - fiori_count

    # QA 6.a: obtener tipo de FUE del rol desde la tabla de licencias (FUE_Rol.xlsx)
    license_row = LicenseRole.query.filter_by(role_name=role_name).first()
    fue_info = {
        "code": license_row.fue_type_code if license_row else None,
        "label": license_row.fue_type_raw if license_row else None,
    }

    # QA: niveles organizacionales del rol (AGR_1252.xlsx/USVAR.xlsx), a
    # nivel cabecera -- ver docstring de _get_role_org_levels
    org_levels = _get_role_org_levels(role_name)

    return render_template(
        "rolesdb/rol.html",
        info=info,
        tcodes=tcodes,
        child_tcodes=child_tcodes,
        fiori_count=fiori_count,
        clasica_count=clasica_count,
        fue_info=fue_info,  # QA 6.a
        org_levels=org_levels,  # QA
    )


@bp.route("/tcode")
@login_required
@permission_required("can_view_rolesdb")
def tcode_search():
    """Resultados de búsqueda por transacción: qué roles la contienen."""
    tcode = (request.args.get("q") or "").strip().upper()
    if not tcode:
        return render_template("rolesdb/tcode.html", tcode="", roles=[], desc=None, es_fiori=False, titulo_fiori="")

    desc_row = SapTcodeDescription.query.filter_by(tcode=tcode).first()
    all_fiori = _all_fiori_tcodes()
    es_fiori = tcode in all_fiori
    global_titles = _fiori_tcode_titles({tcode})
    titulo_fiori = global_titles.get(tcode, "")

    roles = _get_roles_with_tcode(tcode)

    return render_template(
        "rolesdb/tcode.html",
        tcode=tcode,
        roles=roles,
        desc=desc_row.description if desc_row else "",
        es_fiori=es_fiori,
        titulo_fiori=titulo_fiori,
    )


# ---------------------------------------------------------------------------
# Autocomplete API
# ---------------------------------------------------------------------------

@bp.route("/api/roles")
@login_required
@permission_required("can_view_rolesdb")
def api_roles():
    """JSON: lista de roles para autocompletar (max 50 resultados).

    QA: solo se ofrecen roles hijos (no compuestos/padre) que ademas
    empiecen con "Z" (convencion SAP para roles de cliente/custom)."""
    q = (request.args.get("q") or "").strip().upper()
    # Subquery: nombres de rol que actuan como padre de algun otro rol
    parent_names = db.session.query(SapRoleDescription.parent_role).filter(
        SapRoleDescription.parent_role.isnot(None)
    ).distinct()
    query = SapRoleDescription.query.filter(
        SapRoleDescription.role_name.like("Z%"),
        SapRoleDescription.role_name.notin_(parent_names),
    )
    if q:
        query = query.filter(
            db.or_(
                SapRoleDescription.role_name.ilike(f"%{q}%"),
                SapRoleDescription.description.ilike(f"%{q}%"),
            )
        )
    rows = query.order_by(SapRoleDescription.role_name).limit(50).all()
    return jsonify([
        {"role_name": r.role_name, "description": r.description}
        for r in rows
    ])


@bp.route("/api/tcodes")
@login_required
@permission_required("can_view_rolesdb")
def api_tcodes():
    """JSON: lista de tcodes para autocompletar (max 50 resultados).

    Parte del universo de tcodes con rol asignado (SapRoleTcode +
    SapFioriIdTcode). Enriquece con descripcion de TSTCT cuando existe,
    pero incluye el tcode igualmente si no tiene entrada en TSTCT
    (caso: App IDs Fiori como F0797 que no estan en TSTCT pero si en roles).
    """
    q = (request.args.get("q") or "").strip().upper()

    # 1. Universo de tcodes asignados a roles
    from sqlalchemy import union
    assigned_q = db.session.query(SapRoleTcode.tcode.label("tcode")).distinct()
    fiori_q = db.session.query(SapFioriIdTcode.tcode.label("tcode")).distinct()
    all_assigned = union(assigned_q, fiori_q).alias("all_assigned")

    # 2. Filtrar por query (tcode o descripcion TSTCT o titulo Fiori)
    from sqlalchemy import select, outerjoin, or_, cast
    from sqlalchemy import String

    base = (
        db.session.query(
            all_assigned.c.tcode,
            SapTcodeDescription.description,
            SapFioriIdTcode.titulo,
        )
        .outerjoin(SapTcodeDescription, SapTcodeDescription.tcode == all_assigned.c.tcode)
        .outerjoin(SapFioriIdTcode, SapFioriIdTcode.tcode == all_assigned.c.tcode)
    )

    if q:
        base = base.filter(
            db.or_(
                all_assigned.c.tcode.ilike(f"%{q}%"),
                SapTcodeDescription.description.ilike(f"%{q}%"),
                SapFioriIdTcode.titulo.ilike(f"%{q}%"),
            )
        )

    rows = base.order_by(all_assigned.c.tcode).limit(50).all()

    seen = set()
    result = []
    for tcode, desc, titulo in rows:
        if tcode in seen:
            continue
        seen.add(tcode)
        result.append({
            "tcode": tcode,
            "description": desc or titulo or "",
        })
    return jsonify(result)
