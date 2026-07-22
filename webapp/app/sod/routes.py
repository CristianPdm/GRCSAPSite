import csv
import io
import json
from datetime import datetime

import openpyxl
from flask import flash, jsonify, redirect, render_template, request, url_for, Response
from flask_login import current_user, login_required

from app.decorators import permission_required
from app.extensions import db
from app.models import AppSetting, AuditLog
from app.sod import bp
from app.utils.excel import column_index, read_excel_matrix
from app.utils.sap_import import SAP_IMPORT_FOLDER_SETTING, last_import_date, scan_import_folder
from app.sod.docx_export import build_informe_docx
from app.sod.engine import (
    MATRIX_EXCLUDE_OPTIONS,
    _parent_role_names,
    build_audit_report_data,
    build_maps,
    build_matrix_rows,
    build_role_crit,
    build_role_segregation_proposals,
    build_user_risk,
    get_parent_role_alerts,
    get_sod_summary,
    invalidate_analysis_cache,
    run_analysis,
    validate_new_role,
)
from app.sod.importers import (
    import_agr_1251,
    import_agr_buffi,
    import_agr_define,
    import_agr_hier,
    import_agr_tcodes,
    import_agr_users,
    import_pb_c_chipm,
    import_sui_tm_mm_app,
    import_tstct,
    import_usr02,
)
from app.sod.models import (
    SapChipCatalog,
    SapFioriAppReg,
    SapFioriIdTcode,
    SapRoleAssignment,
    SapRoleHierNode,
    SapRoleTableAuth,
    SapRoleTcode,
    SapTcodeDescription,
    SapUserStatus,
    SodException,
    SodRule,
)


def _table_exists(table_name):
    """Comprueba si una tabla existe en la BD (para migraciones aún no aplicadas)."""
    from sqlalchemy import inspect
    insp = inspect(db.engine)
    return table_name in insp.get_table_names()
from app.sod.rules_data import SOD_BASE_RULES


@bp.route("/")
@login_required
@permission_required("can_view_sod")
def index():
    """Punto de entrada del modulo SOD/GRC: resumen + accesos a las
    distintas pantallas (importacion, resultados, reglas, excepciones)."""
    permissions = {
        "puede_ejecutar_analisis": current_user.has_permission("can_run_sod_analysis"),
        "puede_administrar_config": current_user.has_permission("can_manage_sod_config"),
        "puede_exportar": current_user.has_permission("can_export_reports"),
    }
    summary = get_sod_summary()
    return render_template("sod/index.html", permissions=permissions, summary=summary)


@bp.route("/importar", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_sod_config", "can_import_sap_data")
def importar():
    """Carga de archivos Excel exportados de SAP (AGR_USERS, AGR_1251,
    AGR_TCODES, AGR_DEFINE). Cada carga reemplaza por completo los datos
    previos de esa tabla (full refresh), igual que el importador original.

    Ademas de subir cada archivo a mano, se puede configurar una carpeta del
    servidor donde se dejan todos los Excel y disparar una importacion en
    lote que los detecta por nombre (ver app/utils/sap_import.py)."""
    importers = {
        "AGR_USERS": ("Asignaciones Rol-Usuario", import_agr_users),
        "AGR_1251": ("Transacciones por rol (S_TCODE)", import_agr_1251),
        "AGR_TCODES": ("Transacciones por rol (respaldo)", import_agr_tcodes),
        "AGR_DEFINE": ("Descripciones de rol", import_agr_define),
        "TSTCT": ("Descripciones de TCode (opcional)", import_tstct),
        "AGR_HIER": ("Catalogos/grupos Fiori por rol", import_agr_hier),
        "AGR_BUFFI": ("URLs de catalogos/grupos Fiori", import_agr_buffi),
        "PB_C_CHIPM": ("Apps Fiori (chips) por catalogo/grupo", import_pb_c_chipm),
        "SUI_TM_MM_APP": ("Registro de apps Fiori (SUI_TM_MM_APP)", import_sui_tm_mm_app),
        "USR02": ("Estado de cuenta SAP (bloqueo, ultimo login)", import_usr02),
    }

    scan_resultado = None

    if request.method == "POST":
        accion = request.form.get("accion", "subir_archivo")

        if accion == "guardar_carpeta":
            carpeta = request.form.get("carpeta", "").strip()
            AppSetting.set(SAP_IMPORT_FOLDER_SETTING, carpeta)
            AuditLog.log("sap_import_folder_set", username=current_user.username,
                         details=f"Carpeta de importacion SAP: {carpeta or '(vacia)'}")
            flash("Carpeta de importación guardada." if carpeta else "Carpeta de importación borrada.", "success")
            return redirect(url_for("sod.importar"))

        if accion == "importar_carpeta":
            carpeta = AppSetting.get(SAP_IMPORT_FOLDER_SETTING, "")
            scan_resultado = scan_import_folder(carpeta, importers)

            if scan_resultado is None:
                flash("La carpeta configurada no existe o no es accesible desde el servidor.", "error")
            else:
                for item in scan_resultado:
                    if item["ok"]:
                        AuditLog.log("sod_import", username=current_user.username,
                                     details=f"{item['tipo']}: {item['count']} filas importadas ({item['filename']}, vía carpeta)")

                ok_count = sum(1 for item in scan_resultado if item["ok"])
                err_count = sum(1 for item in scan_resultado if item["found"] and not item["ok"])
                missing_count = sum(1 for item in scan_resultado if not item["found"])

                if ok_count:
                    flash(f"{ok_count} archivo(s) importado(s) correctamente desde la carpeta.", "success")
                if err_count:
                    flash(f"{err_count} archivo(s) encontrados con errores al importar (ver detalle abajo).", "error")
                if missing_count:
                    flash(f"{missing_count} archivo(s) esperados no se encontraron en la carpeta.", "warning")
        else:
            tipo = request.form.get("tipo")
            archivo = request.files.get("archivo")

            if not archivo or not archivo.filename:
                flash("Selecciona un archivo .xlsx para importar.", "error")
                return redirect(url_for("sod.importar"))

            if tipo not in importers:
                flash("Tipo de archivo no reconocido.", "error")
                return redirect(url_for("sod.importar"))

            label, importer_func = importers[tipo]
            try:
                count = importer_func(archivo.stream)
            except Exception as exc:
                flash(f"Error al importar {label}: {exc}", "error")
                return redirect(url_for("sod.importar"))

            AuditLog.log("sod_import", username=current_user.username,
                         details=f"{tipo}: {count} filas importadas ({archivo.filename})")
            flash(f"{label}: {count} filas importadas correctamente.", "success")
            return redirect(url_for("sod.importar"))

    stats = {
        "agr_users": SapRoleAssignment.query.count(),
        "agr_1251": SapRoleTcode.query.filter_by(source="AGR_1251").count(),
        "agr_tcodes": SapRoleTcode.query.filter_by(source="AGR_TCODES").count(),
        "tabu_dis": SapRoleTableAuth.query.count(),
        "tstct": SapTcodeDescription.query.count(),
        "agr_hier": SapRoleHierNode.query.count(),
        "pb_c_chipm": SapChipCatalog.query.count() if _table_exists("sap_chip_catalog") else SapFioriIdTcode.query.count(),
        "sui_tm_mm_app": SapFioriAppReg.query.count() if _table_exists("sap_fiori_app_reg") else 0,
        "fiori_tcodes": SapRoleTcode.query.filter_by(source="FIORI").count(),
        "usr02": SapUserStatus.query.count(),
        "usr02_bloqueados": SapUserStatus.query.filter_by(bloqueado=True).count(),
    }
    carpeta = AppSetting.get(SAP_IMPORT_FOLDER_SETTING, "")
    return render_template("sod/import.html", stats=stats, carpeta=carpeta, scan_resultado=scan_resultado)


@bp.route("/resultados")
@login_required
@permission_required("can_view_sod")
def resultados():
    """Resultados del analisis SOD: conflictos activos por regla, agrupados
    por nivel de severidad. Se recalcula en cada visita (no se persiste el
    resultado, solo las reglas/excepciones que lo determinan)."""
    results = run_analysis()
    modulos = sorted({item["rule"].modulo for item in results if item["rule"].modulo})
    return render_template("sod/results.html", results=results, modulos=modulos)


@bp.route("/resultados/exportar.csv")
@login_required
@permission_required("can_export_reports")
@permission_required("can_view_sod")
def exportar_resultados():
    """Exporta los conflictos activos (no exceptuados) a CSV. Requiere
    ademas can_view_sod (no solo can_export_reports): solo se puede
    exportar lo que tambien se tiene permiso de ver en pantalla (/resultados)."""
    results = run_analysis()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Regla", "Modulo", "Nivel", "Descripcion", "Usuario"])
    for item in results:
        rule = item["rule"]
        for username in item["conflicted_users"]:
            writer.writerow([rule.id, rule.modulo, rule.nivel, rule.descripcion, username])

    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=sod_conflictos.csv"},
    )


@bp.route("/usuarios-riesgo")
@login_required
@permission_required("can_view_sod")
def usuarios_riesgo():
    """Vista por usuario: conflictos SOD, FUE derivado de sus roles y (si
    hay datos de Licencias importados) FUE oficial e inactividad SAP.

    usr02_updated es la fecha de la ultima importacion de USR02.xlsx (de
    donde sale 'Ultimo login (SAP)'): se muestra arriba de la lista para
    que no se confunda con la fecha de hoy -- la tabla puede tener semanas
    sin actualizarse aunque el usuario la siga consultando a diario."""
    rows = build_user_risk()
    usr02_updated = last_import_date(SapUserStatus)
    return render_template("sod/usuarios_riesgo.html", rows=rows, usr02_updated=usr02_updated)


@bp.route("/roles-criticos")
@login_required
@permission_required("can_view_sod")
def roles_criticos():
    """Vista por rol: severidad SOD, FUE de referencia, usuarios
    asignados, tcodes y bandera de acceso irrestricto a tablas. Los roles
    padre/compuestos se excluyen de la lista (ver build_role_crit); el
    warning de asignacion anomala a un rol padre se muestra solo en
    Matriz (ver matriz())."""
    rows = build_role_crit()
    return render_template("sod/roles_criticos.html", rows=rows)


@bp.route("/segregacion")
@login_required
@permission_required("can_view_sod")
def segregacion():
    """Propuestas de segregacion: roles que habilitan, por si mismos,
    transacciones de ambos lados de una regla SOD activa (autoconflicto).
    Para cada uno, sugiere como dividirlo en dos roles para cumplir con la
    matriz SOD -- a diferencia de 'Roles criticos', que lista cualquier rol
    involucrado en un conflicto (aunque el conflicto solo aparezca al
    combinarlo con otro rol del usuario), aqui solo entran los roles cuyo
    propio diseno ya viola la segregacion de funciones. Los roles padre/
    compuestos se excluyen (ver build_role_segregation_proposals); el
    warning de asignacion anomala a un rol padre se muestra solo en
    Matriz (ver matriz())."""
    rows = build_role_segregation_proposals()
    return render_template("sod/segregacion.html", rows=rows)


@bp.route("/validar", methods=["GET", "POST"])
@login_required
@permission_required("can_view_sod")
def validar():
    """Simulador 'Validar nuevo rol': antes de asignar roles nuevos a un
    usuario en SAP, simula el efecto sobre los conflictos SOD y el tipo
    FUE resultante, y devuelve una recomendacion (RECHAZAR / EXCEPCION
    DOCUMENTADA / REVISAR FUE / APROBAR)."""
    _, r2u = build_maps()
    usuarios_conocidos = sorted({u for users in r2u.values() for u in users})

    resultado = None
    username_in = ""
    roles_in = ""
    if request.method == "POST":
        username_in = request.form.get("username", "").strip()
        roles_in = request.form.get("roles", "")
        roles_list = [r for r in roles_in.split(",") if r.strip()]
        if not username_in or not roles_list:
            flash("Indica el usuario y al menos un rol nuevo a simular.", "error")
        else:
            resultado = validate_new_role(username_in, roles_list)

    return render_template(
        "sod/validar.html",
        usuarios_conocidos=usuarios_conocidos,
        resultado=resultado,
        username_in=username_in,
        roles_in=roles_in,
    )


@bp.route("/validar/roles.json")
@login_required
@permission_required("can_view_sod")
def validar_roles_json():
    """Lista de roles conocidos (asignados y/o con tcodes) para el picker
    del simulador de validacion de nuevo rol. Excluye roles padre (compuestos)
    porque no tienen tcodes propios y no generan conflictos SOD directos."""
    _, r2u = build_maps()
    role_tcodes_roles = {row[0] for row in db.session.query(SapRoleTcode.role_name).distinct()}
    parent_roles = _parent_role_names()
    todos = (set(r2u.keys()) | role_tcodes_roles) - parent_roles
    return jsonify(sorted(todos))


@bp.route("/matriz")
@login_required
@permission_required("can_view_sod")
def matriz():
    """Matriz Usuario x Rol x TCode: una fila por cada tcode habilitado de
    cada rol activo de cada usuario. Filtros por substring o wildcard (*, ?)
    (usuario, rol, tcode) via query string, aplicados en el servidor; la
    vista se limita a las primeras 2000 filas del resultado filtrado
    (igual que el original, que truncaba la tabla en el navegador).
    Si la request viene de matrix-filters.js (busqueda incremental, header
    X-Requested-With) se devuelve solo el fragmento de resultados en vez
    de la pagina completa, para actualizar la tabla sin recargar."""
    f_user = request.args.get("u", "")
    f_rol = request.args.get("r", "")
    f_tc = request.args.get("t", "")
    excl = request.args.get("excl", "")
    if excl not in MATRIX_EXCLUDE_OPTIONS:
        excl = ""
    rows = build_matrix_rows(f_user, f_rol, f_tc, excl)

    contexto = dict(rows=rows[:2000], total=len(rows), f_user=f_user, f_rol=f_rol, f_tc=f_tc, excl=excl)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render_template("sod/_matriz_rows.html", **contexto)
    contexto["parent_alerts"] = get_parent_role_alerts()
    return render_template("sod/matriz.html", **contexto)


@bp.route("/matriz/exportar.csv")
@login_required
@permission_required("can_export_reports")
@permission_required("can_view_sod")
def matriz_exportar():
    """Exporta la matriz completa (sin filtrar) Usuario x Rol x TCode.
    Requiere ademas can_view_sod, igual que la pantalla /matriz."""
    rows = build_matrix_rows()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Usuario", "Rol", "TCode", "Descripcion"])
    for row in rows:
        writer.writerow([row["user"], row["rol"], row["tc"], row["desc"]])

    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=Matriz_Usuario_Rol_TCode.csv"},
    )


@bp.route("/reglas")
@login_required
@permission_required("can_manage_sod_config")
def reglas():
    """Matriz de reglas SOD, agrupada por modulo."""
    rules = SodRule.query.order_by(SodRule.modulo, SodRule.id).all()
    modulos = sorted({rule.modulo for rule in rules if rule.modulo})
    return render_template("sod/rules.html", rules=rules, modulos=modulos)


@bp.route("/reglas/tcodes.json")
@login_required
@permission_required("can_manage_sod_config")
def tcodes_json():
    """Lista de TCodes conocidos para el buscador del picker en el
    formulario de reglas: union de los importados (AGR_1251/AGR_TCODES)
    y los ya usados en alguna regla SOD, para que el picker tenga
    contenido aun sin datos importados todavia.

    Incluye la descripcion corta de TSTCT.xlsx (SapTcodeDescription) solo
    como ayuda visual en el desplegable de busqueda -- el picker sigue
    guardando y mostrando unicamente el codigo en el chip seleccionado,
    la descripcion no se persiste en la regla.

    TSTCT solo cubre transacciones clasicas. Para un tcode resuelto desde
    PB_C_CHIPM (Fiori) que no tenga descripcion alli, se usa de respaldo
    el titulo del tile ('display_title_text', columna CONFIGURATION),
    guardado en SapFioriIdTcode.titulo al importar."""
    importados = {row[0] for row in db.session.query(SapRoleTcode.tcode).distinct()}
    usados_en_reglas = set()
    for rule in SodRule.query.all():
        usados_en_reglas.update(rule.tcodes1_list())
        usados_en_reglas.update(rule.tcodes2_list())
    todos = sorted(importados | usados_en_reglas)

    descripciones = {
        row.tcode: row.description
        for row in SapTcodeDescription.query.filter(SapTcodeDescription.tcode.in_(todos)).all()
    }

    titulos_fiori = {}
    for row in (
        SapFioriIdTcode.query.filter(SapFioriIdTcode.tcode.in_(todos), SapFioriIdTcode.titulo != "")
        .all()
    ):
        titulos_fiori.setdefault(row.tcode, row.titulo)

    return jsonify([
        {"tcode": tc, "descripcion": descripciones.get(tc) or titulos_fiori.get(tc, "")}
        for tc in todos
    ])


@bp.route("/reglas/nueva", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_sod_config")
def regla_nueva():
    if request.method == "POST":
        rule_id = request.form.get("id", "").strip().upper()
        if not rule_id:
            flash("El ID de la regla es obligatorio.", "error")
            return redirect(url_for("sod.regla_nueva"))
        if SodRule.query.get(rule_id):
            flash(f"Ya existe una regla con ID {rule_id}.", "error")
            return redirect(url_for("sod.regla_nueva"))

        rule = SodRule(
            id=rule_id,
            modulo=request.form.get("modulo", "").strip().upper(),
            nivel=request.form.get("nivel", "MEDIO"),
            descripcion=request.form.get("descripcion", "").strip(),
            permiso1=request.form.get("permiso1", "").strip(),
            permiso2=request.form.get("permiso2", "").strip(),
            origen="CUSTOM",
        )
        rule.set_tcodes1(request.form.get("tcodes1", "").split(","))
        rule.set_tcodes2(request.form.get("tcodes2", "").split(","))
        db.session.add(rule)
        db.session.commit()
        invalidate_analysis_cache()

        AuditLog.log("sod_rule_create", username=current_user.username, details=f"Regla {rule_id} creada")
        flash(f"Regla {rule_id} creada.", "success")
        return redirect(url_for("sod.reglas"))

    return render_template("sod/rule_form.html", rule=None)


@bp.route("/reglas/<rule_id>/editar", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_sod_config")
def regla_editar(rule_id):
    rule = SodRule.query.get_or_404(rule_id)

    if request.method == "POST":
        rule.modulo = request.form.get("modulo", "").strip().upper()
        rule.nivel = request.form.get("nivel", rule.nivel)
        rule.descripcion = request.form.get("descripcion", "").strip()
        rule.permiso1 = request.form.get("permiso1", "").strip()
        rule.permiso2 = request.form.get("permiso2", "").strip()
        rule.set_tcodes1(request.form.get("tcodes1", "").split(","))
        rule.set_tcodes2(request.form.get("tcodes2", "").split(","))
        db.session.commit()
        invalidate_analysis_cache()

        AuditLog.log("sod_rule_edit", username=current_user.username, details=f"Regla {rule_id} editada")
        flash(f"Regla {rule_id} actualizada.", "success")
        return redirect(url_for("sod.reglas"))

    return render_template("sod/rule_form.html", rule=rule)


@bp.route("/reglas/<rule_id>/toggle", methods=["POST"])
@login_required
@permission_required("can_manage_sod_config")
def regla_toggle(rule_id):
    rule = SodRule.query.get_or_404(rule_id)
    rule.activo = not rule.activo
    db.session.commit()
    invalidate_analysis_cache()

    AuditLog.log("sod_rule_toggle", username=current_user.username,
                 details=f"Regla {rule_id} {'activada' if rule.activo else 'desactivada'}")
    return redirect(url_for("sod.reglas"))


@bp.route("/reglas/exportar.json")
@login_required
@permission_required("can_export_reports")
@permission_required("can_manage_sod_config")
def reglas_exportar_json():
    """Exporta la matriz de reglas SOD completa a JSON, con las mismas
    claves que usaba SAP_SOD_Analyzer_v640.html (id/niv/desc/p1/t1/p2/t2/
    activo/origen), para que un archivo exportado del Analyzer original
    pueda re-importarse aqui y viceversa. Requiere ademas
    can_manage_sod_config, igual que la pantalla /reglas: un Auditor con
    can_export_reports pero sin acceso a /reglas no debe poder descargar
    la matriz completa de reglas."""
    rules = SodRule.query.order_by(SodRule.id).all()
    data = [
        {
            "id": rule.id,
            "niv": rule.nivel,
            "desc": rule.descripcion,
            "p1": rule.permiso1,
            "t1": rule.tcodes1_list(),
            "p2": rule.permiso2,
            "t2": rule.tcodes2_list(),
            "activo": rule.activo,
            "origen": rule.origen,
        }
        for rule in rules
    ]

    fecha = datetime.utcnow().strftime("%Y-%m-%d")
    return Response(
        json.dumps(data, indent=2, ensure_ascii=False),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename=SOD_Matrix_{fecha}.json"},
    )


@bp.route("/reglas/exportar.xlsx")
@login_required
@permission_required("can_export_reports")
@permission_required("can_manage_sod_config")
def reglas_exportar_excel():
    """Exporta la matriz de reglas SOD a Excel (formato pensado para
    Auditoria Interna, igual que el boton 'Exportar Excel' del original).
    Requiere ademas can_manage_sod_config, igual que la pantalla /reglas."""
    rules = SodRule.query.order_by(SodRule.id).all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Matriz SOD"
    ws.append(["ID", "Modulo", "Nivel de Riesgo", "Descripcion", "Proceso A",
               "TCodes A", "Proceso B", "TCodes B", "Estado", "Origen"])
    for rule in rules:
        ws.append([
            rule.id, rule.modulo, rule.nivel, rule.descripcion,
            rule.permiso1, ", ".join(rule.tcodes1_list()),
            rule.permiso2, ", ".join(rule.tcodes2_list()),
            "Activa" if rule.activo else "Inactiva", rule.origen,
        ])
    ws.auto_filter.ref = ws.dimensions
    for col_idx, width in enumerate([12, 8, 14, 50, 24, 22, 24, 22, 10, 12], start=1):
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    fecha = datetime.utcnow().strftime("%Y-%m-%d")
    return Response(
        buffer.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=Matriz_SOD_{fecha}.xlsx"},
    )


@bp.route("/reglas/importar", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_sod_config")
def reglas_importar():
    """Importa una matriz SOD completa desde un archivo JSON (exportado con
    'Exportar JSON' desde esta app o desde SAP_SOD_Analyzer_v640.html).
    Reemplaza por completo las reglas existentes -- el original tampoco
    fusionaba, sobrescribia todo el array SOD en memoria."""
    if request.method == "POST":
        archivo = request.files.get("archivo")
        if not archivo or not archivo.filename:
            flash("Selecciona un archivo .json para importar.", "error")
            return redirect(url_for("sod.reglas_importar"))

        try:
            data = json.load(archivo.stream)
        except Exception as exc:
            flash(f"El archivo no es un JSON válido: {exc}", "error")
            return redirect(url_for("sod.reglas_importar"))

        required = {"id", "niv", "desc", "p1", "t1", "p2", "t2"}
        if not isinstance(data, list) or not data or not required.issubset(data[0].keys()):
            flash("Formato de regla incorrecto. Se esperaba un array con id/niv/desc/p1/t1/p2/t2.", "error")
            return redirect(url_for("sod.reglas_importar"))

        SodRule.query.delete()
        for item in data:
            rule = SodRule(
                id=item["id"],
                modulo=item.get("modulo") or str(item["id"]).split("-")[0],
                nivel=item["niv"],
                descripcion=item["desc"],
                permiso1=item.get("p1", ""),
                permiso2=item.get("p2", ""),
                activo=item.get("activo", True),
                origen=item.get("origen", "CUSTOM"),
            )
            rule.set_tcodes1(item.get("t1", []))
            rule.set_tcodes2(item.get("t2", []))
            db.session.add(rule)
        db.session.commit()
        invalidate_analysis_cache()

        AuditLog.log("sod_rule_import", username=current_user.username,
                     details=f"Matriz SOD importada desde JSON: {len(data)} reglas (reemplazo total)")
        flash(f"Matriz SOD importada: {len(data)} reglas. Se reemplazó la matriz anterior.", "success")
        return redirect(url_for("sod.reglas"))

    return render_template("sod/rules_import.html")


@bp.route("/reglas/importar.xlsx", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_sod_config")
def reglas_importar_excel():
    """Importa la matriz SOD completa desde un Excel con el mismo formato
    que genera 'Exportar Excel' (columnas ID/Modulo/Nivel de Riesgo/
    Descripcion/Proceso A/TCodes A/Proceso B/TCodes B/Estado/Origen), para
    que un auditor pueda bajar la matriz, editarla en Excel (modificar
    reglas, agregar tcodes, activar/desactivar) y volver a subirla sin
    pasar por el formulario regla por regla.

    Las apps Fiori no necesitan tratamiento especial: en las columnas
    'TCodes A'/'TCodes B' se escriben como un codigo mas, separado por
    comas, igual que un tcode clasico. El motor SOD ya unifica ambos
    origenes en una sola lista de tcodes por rol antes de comparar contra
    la matriz (ver importers.recompute_fiori_tcodes), asi que una regla no
    distingue si el tcode vino de AGR_1251/AGR_TCODES o de un catalogo/
    grupo Fiori (PB_C_CHIPM).

    Reemplaza por completo las reglas existentes -- igual que la
    importacion JSON, no se fusiona con la matriz actual."""
    if request.method == "POST":
        archivo = request.files.get("archivo")
        if not archivo or not archivo.filename:
            flash("Selecciona un archivo .xlsx para importar.", "error")
            return redirect(url_for("sod.reglas_importar"))

        try:
            headers, rows = read_excel_matrix(archivo.stream)
        except Exception as exc:
            flash(f"No se pudo leer el archivo Excel: {exc}", "error")
            return redirect(url_for("sod.reglas_importar"))

        id_idx = column_index(headers, ["ID"])
        nivel_idx = column_index(headers, ["NIVEL DE RIESGO", "NIVEL"])
        desc_idx = column_index(headers, ["DESCRIPCION"])
        modulo_idx = column_index(headers, ["MODULO"])
        p1_idx = column_index(headers, ["PROCESO A"])
        t1_idx = column_index(headers, ["TCODES A"])
        p2_idx = column_index(headers, ["PROCESO B"])
        t2_idx = column_index(headers, ["TCODES B"])
        estado_idx = column_index(headers, ["ESTADO"])
        origen_idx = column_index(headers, ["ORIGEN"])

        if id_idx is None or nivel_idx is None or desc_idx is None:
            flash("Formato de Excel incorrecto. Se esperan, como mínimo, las columnas ID, Nivel de Riesgo y Descripción (igual que el archivo de 'Exportar Excel').", "error")
            return redirect(url_for("sod.reglas_importar"))

        def _cell(row, idx):
            if idx is None or idx >= len(row) or row[idx] is None:
                return ""
            return str(row[idx]).strip()

        nuevas = []
        for row in rows:
            rule_id = _cell(row, id_idx).upper()
            if not rule_id:
                continue
            nuevas.append(dict(
                id=rule_id,
                modulo=_cell(row, modulo_idx).upper() or rule_id.split("-")[0],
                nivel=_cell(row, nivel_idx).upper() or "MEDIO",
                descripcion=_cell(row, desc_idx),
                permiso1=_cell(row, p1_idx),
                tcodes1=[c.strip() for c in _cell(row, t1_idx).split(",") if c.strip()],
                permiso2=_cell(row, p2_idx),
                tcodes2=[c.strip() for c in _cell(row, t2_idx).split(",") if c.strip()],
                activo=_cell(row, estado_idx).upper() != "INACTIVA",
                origen=_cell(row, origen_idx) or "CUSTOM",
            ))

        if not nuevas:
            flash("El archivo no tiene filas de reglas para importar.", "error")
            return redirect(url_for("sod.reglas_importar"))

        SodRule.query.delete()
        for item in nuevas:
            rule = SodRule(
                id=item["id"], modulo=item["modulo"], nivel=item["nivel"],
                descripcion=item["descripcion"], permiso1=item["permiso1"],
                permiso2=item["permiso2"], activo=item["activo"], origen=item["origen"],
            )
            rule.set_tcodes1(item["tcodes1"])
            rule.set_tcodes2(item["tcodes2"])
            db.session.add(rule)
        db.session.commit()
        invalidate_analysis_cache()

        AuditLog.log("sod_rule_import", username=current_user.username,
                     details=f"Matriz SOD importada desde Excel: {len(nuevas)} reglas (reemplazo total)")
        flash(f"Matriz SOD importada: {len(nuevas)} reglas. Se reemplazó la matriz anterior.", "success")
        return redirect(url_for("sod.reglas"))

    return redirect(url_for("sod.reglas_importar"))


@bp.route("/reglas/restablecer", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_sod_config")
def reglas_restablecer():
    """Restablece la matriz SOD a las reglas base originales (rules_data.py),
    perdiendo cualquier regla personalizada, edicion o desactivacion. GET
    muestra la pantalla de confirmacion; POST ejecuta el restablecimiento
    (reemplaza al confirm() de JS del original, que no aplica server-side)."""
    if request.method == "POST":
        SodRule.query.delete()
        for item in SOD_BASE_RULES:
            rule = SodRule(
                id=item["id"], modulo=item["modulo"], nivel=item["nivel"],
                descripcion=item["desc"], permiso1=item["p1"], permiso2=item["p2"],
                origen="BASE",
            )
            rule.set_tcodes1(item["t1"])
            rule.set_tcodes2(item["t2"])
            db.session.add(rule)
        db.session.commit()
        invalidate_analysis_cache()

        AuditLog.log("sod_reset", username=current_user.username,
                     details=f"Matriz SOD restablecida a {len(SOD_BASE_RULES)} reglas base")
        flash(f"Matriz SOD restablecida a las {len(SOD_BASE_RULES)} reglas base originales.", "success")
        return redirect(url_for("sod.reglas"))

    return render_template("sod/rules_reset.html", total_base=len(SOD_BASE_RULES))


@bp.route("/reglas/log")
@login_required
@permission_required("can_manage_sod_config")
def reglas_log():
    """Log de cambios de la matriz SOD: altas, ediciones, activaciones,
    importaciones y restablecimientos de reglas (filtra el audit log
    general de la app a las acciones de este modulo)."""
    acciones_sod = ("sod_rule_create", "sod_rule_edit", "sod_rule_toggle",
                    "sod_rule_import", "sod_reset")
    entries = (
        AuditLog.query
        .filter(AuditLog.action.in_(acciones_sod))
        .order_by(AuditLog.timestamp.desc())
        .limit(200)
        .all()
    )
    return render_template("sod/rules_log.html", entries=entries)


@bp.route("/informe")
@login_required
@permission_required("can_export_reports")
@permission_required("can_view_sod")
def informe():
    """Informe de Auditoria GRC-SOD: documento HTML independiente (con su
    propia hoja de estilos, pensado para 'Imprimir / Guardar PDF' desde el
    navegador), equivalente a expReport() de SAP_SOD_Analyzer_v640.html.
    No extiende base.html porque es un documento de salida (como el que el
    original abria en una ventana aparte con window.open()), no una vista
    mas de la aplicacion. Requiere ademas can_view_sod: el informe agrega
    datos (conflictos, usuarios en riesgo, roles criticos) que en pantalla
    estan protegidos por ese permiso."""
    data = build_audit_report_data()
    has_alertas = bool(data["criticos_activos"])
    sec = {
        "resumen": 1,
        "alertas": 2 if has_alertas else None,
        "sod": 3 if has_alertas else 2,
        "usuarios": 4 if has_alertas else 3,
        "roles": 5 if has_alertas else 4,
        "fue": 6 if has_alertas else 5,
        "matriz": 7 if has_alertas else 6,
        "recos": 8 if has_alertas else 7,
    }

    return render_template("sod/informe.html", data=data, sec=sec,
                            has_alertas=has_alertas, ahora=datetime.utcnow())


@bp.route("/informe.docx")
@login_required
@permission_required("can_export_reports")
@permission_required("can_view_sod")
def informe_word():
    """Exporta el Informe de Auditoria GRC-SOD en formato Word (.docx),
    equivalente a expWord() de SAP_SOD_Analyzer_v640.html. Incluye, ademas
    de lo que ya tiene el informe HTML, el detalle de Optimizacion FUE
    (candidatos a baja/downgrade), igual que hacia el original. Requiere
    ademas can_view_sod, mismo motivo que /informe."""
    buffer = build_informe_docx()
    fecha = datetime.utcnow().strftime("%Y-%m-%d")
    return Response(
        buffer.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename=Informe_GRC_SOD_{fecha}.docx"},
    )


@bp.route("/excepciones")
@login_required
@permission_required("can_manage_sod_config")
def excepciones():
    items = SodException.query.order_by(SodException.fecha.desc()).all()
    rules = SodRule.query.order_by(SodRule.id).all()

    # Usuarios con conflicto activo (sin excepcion todavia) por regla, para
    # poblar el selector de usuario una vez elegida la regla en el formulario.
    analysis = run_analysis()
    users_by_rule = {item["rule"].id: item["conflicted_users"] for item in analysis}

    return render_template(
        "sod/exceptions.html", items=items, rules=rules, users_by_rule=users_by_rule
    )


@bp.route("/excepciones/nueva", methods=["POST"])
@login_required
@permission_required("can_manage_sod_config")
def excepcion_nueva():
    regla_id = request.form.get("regla_id", "").strip()
    usuario = request.form.get("usuario", "").strip()
    motivo = request.form.get("motivo", "").strip()

    rule = SodRule.query.get(regla_id)
    if not rule or not usuario or not motivo:
        flash("Regla, usuario y motivo son obligatorios.", "error")
        return redirect(url_for("sod.excepciones"))

    existing = SodException.query.filter_by(regla_id=regla_id, usuario=usuario).first()
    if existing:
        flash(f"Ya existe una excepción para {usuario} en la regla {regla_id}.", "error")
        return redirect(url_for("sod.excepciones"))

    exception = SodException(
        regla_id=regla_id,
        usuario=usuario,
        nivel_original=rule.nivel,
        motivo=motivo,
        creado_por=current_user.username,
    )
    db.session.add(exception)
    db.session.commit()
    invalidate_analysis_cache()

    AuditLog.log("sod_exception_create", username=current_user.username,
                 details=f"Excepción creada: {usuario} / {regla_id}")
    flash("Excepción registrada.", "success")
    return redirect(url_for("sod.excepciones"))


@bp.route("/excepciones/<int:exception_id>/eliminar", methods=["POST"])
@login_required
@permission_required("can_manage_sod_config")
def excepcion_eliminar(exception_id):
    exception = SodException.query.get_or_404(exception_id)
    detalle = f"{exception.usuario} / {exception.regla_id}"
    db.session.delete(exception)
    db.session.commit()
    invalidate_analysis_cache()

    AuditLog.log("sod_exception_delete", username=current_user.username, details=f"Excepción eliminada: {detalle}")
    flash("Excepción eliminada.", "success")
    return redirect(url_for("sod.excepciones"))
