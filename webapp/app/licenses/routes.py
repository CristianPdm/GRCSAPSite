from datetime import date

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.decorators import permission_required
from app.licenses import bp
from app.licenses.engine import build_fue_comparison, compute_fue_optimization, get_license_summary
from app.licenses.importers import import_fue_roles, import_fue_users
from app.licenses.models import LicenseRole, LicenseUser
from app.licenses.rules import FUE_LABEL, INACTIVITY_THRESHOLD_DAYS
from app.models import AppSetting, AuditLog
from app.sod.models import SapRoleAssignment, SapUserStatus
from app.utils.sap_import import SAP_IMPORT_FOLDER_SETTING, last_import_date, scan_import_folder


@bp.route("/")
@login_required
@permission_required("can_view_licenses")
def index():
    """Punto de entrada del modulo de Licencias SAP: resumen de consumo de
    FUEs y accesos a importacion / listado de usuarios."""
    puede_administrar = current_user.has_permission("can_manage_licenses")
    summary = get_license_summary()
    return render_template("licenses/index.html", puede_administrar=puede_administrar, summary=summary)


@bp.route("/importar", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_licenses", "can_import_sap_data")
def importar():
    """Carga de FUE_Rol.xlsx y FUE_Users.xlsx. FUE_Users.xlsx es la fuente
    canonica del calculo de licencias; FUE_Rol.xlsx queda como referencia.

    Tambien admite importar ambos desde una carpeta configurable del
    servidor (la misma que usa el modulo SOD), detectando los archivos por
    nombre en lugar de subirlos de a uno (ver app/utils/sap_import.py)."""
    importers = {
        "FUE_ROL": ("Tipo FUE por rol", import_fue_roles),
        "FUE_USERS": ("Tipo FUE oficial por usuario", import_fue_users),
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
            return redirect(url_for("licenses.importar"))

        if accion == "importar_carpeta":
            carpeta = AppSetting.get(SAP_IMPORT_FOLDER_SETTING, "")
            scan_resultado = scan_import_folder(carpeta, importers)

            if scan_resultado is None:
                flash("La carpeta configurada no existe o no es accesible desde el servidor.", "error")
            else:
                for item in scan_resultado:
                    if item["ok"]:
                        AuditLog.log("license_import", username=current_user.username,
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
                return redirect(url_for("licenses.importar"))

            if tipo not in importers:
                flash("Tipo de archivo no reconocido.", "error")
                return redirect(url_for("licenses.importar"))

            label, importer_func = importers[tipo]
            try:
                count = importer_func(archivo.stream)
            except Exception as exc:
                flash(f"Error al importar {label}: {exc}", "error")
                return redirect(url_for("licenses.importar"))

            AuditLog.log("license_import", username=current_user.username,
                         details=f"{tipo}: {count} filas importadas ({archivo.filename})")
            flash(f"{label}: {count} filas importadas correctamente.", "success")
            return redirect(url_for("licenses.importar"))

    stats = {
        "fue_roles": LicenseRole.query.count(),
        "fue_users": LicenseUser.query.count(),
    }
    carpeta = AppSetting.get(SAP_IMPORT_FOLDER_SETTING, "")
    return render_template("licenses/import.html", stats=stats, carpeta=carpeta, scan_resultado=scan_resultado)


@bp.route("/usuarios")
@login_required
@permission_required("can_view_licenses")
def usuarios():
    """Listado de usuarios con su tipo FUE oficial e indicador de
    inactividad (licenciado pero sin acceso reciente).

    usr02_usernames son los usuarios presentes en USR02 (tabla maestra de
    usuarios SAP, importada en el modulo SOD): si un usuario con licencia
    FUE no figura ahi, es una inconsistencia entre el reporte de
    licenciamiento y SAP (usuario renombrado, eliminado, etc.) que el
    template marca con un warning. usr02_imported evita falsos positivos
    cuando USR02 todavia no se importo (lista vacia no implica que ningun
    usuario exista en SAP).

    license_updated es la fecha de la ultima importacion de FUE_Users.xlsx
    (de donde sale 'Último acceso'): se muestra arriba de la lista para no
    confundir una tabla desactualizada con inactividad real del usuario."""
    users = LicenseUser.query.order_by(LicenseUser.username).all()
    usr02_usernames = {u.username for u in SapUserStatus.query.all()}
    return render_template(
        "licenses/users.html",
        users=users,
        fue_labels=FUE_LABEL,
        inactivity_days=INACTIVITY_THRESHOLD_DAYS,
        today=date.today(),
        usr02_usernames=usr02_usernames,
        usr02_imported=bool(usr02_usernames),
        license_updated=last_import_date(LicenseUser),
    )


@bp.route("/fue-comparativa")
@login_required
@permission_required("can_view_licenses")
def fue_comparativa():
    """Comparativa FUE oficial (SAP, lo que se factura) vs. FUE derivado
    de los roles activos de cada usuario (modulo SOD)."""
    rows = build_fue_comparison()
    return render_template("licenses/fue_comparativa.html", rows=rows)


@bp.route("/fue-optimizacion")
@login_required
@permission_required("can_view_licenses")
def fue_optimizacion():
    """Candidatos a optimizacion de licencias: usuarios inactivos con FUE
    pagado y usuarios con FUE oficial mayor al que les corresponderia
    segun sus roles activos (downgrade), con el ahorro estimado en FUE.

    license_updated: ver docstring de usuarios() -- misma fuente
    (FUE_Users.xlsx) y mismo motivo para mostrar la fecha de actualizacion."""
    opt = compute_fue_optimization()
    return render_template(
        "licenses/fue_optimizacion.html", opt=opt, license_updated=last_import_date(LicenseUser)
    )


@bp.route("/roles-fue")
@login_required
@permission_required("can_view_licenses")
def roles_fue():
    """QA 5.a: Listado de roles con su nivel de FUE asignado (FUE_Rol.xlsx).
    Permite ver qué tipo de licencia genera cada rol sin necesidad de
    cruzar con usuarios -- útil para auditoría de catálogo de roles."""
    # QA 5.a: nueva vista Roles y tipo de FUE.
    # Solo roles que empiecen con Z (convención SAP para roles cliente)
    # y que tengan al menos una asignación de usuario vigente en AGR_USERS.
    # BUG FIX: with_entities solo proyectaba role_name, pero el filtro
    # intentaba leer row.valid_to (no incluido en la query) -> AttributeError.
    # Se agrega valid_to a la proyección para poder filtrar vigencia.
    today = date.today()
    roles_con_usuarios = {
        row.role_name
        for row in SapRoleAssignment.query.with_entities(
            SapRoleAssignment.role_name, SapRoleAssignment.valid_to
        ).all()
        if (row.valid_to is None or row.valid_to >= today)
    }
    roles = [
        r for r in LicenseRole.query.filter(
            LicenseRole.role_name.like("Z%")
        ).order_by(LicenseRole.role_name).all()
        if r.role_name in roles_con_usuarios
    ]
    fue_label_map = FUE_LABEL
    return render_template("licenses/roles_fue.html", roles=roles, fue_label_map=fue_label_map)
