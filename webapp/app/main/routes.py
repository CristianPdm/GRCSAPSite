from flask_login import current_user, login_required

from app.licenses.routes import get_license_summary
from app.main import bp
from app.sod.routes import get_sod_summary
from flask import render_template


@bp.route("/")
@login_required
def dashboard():
    """Panel principal. El contenido visible depende de los permisos del rol.

    Las pantallas de administracion (Usuarios, Roles, Auditoria) ya no se
    muestran aqui como tiles: se accede a ellas desde el boton
    "Configuracion" de la barra de navegacion, visible solo para quienes
    tengan permisos de administracion (ver templates/_navbar.html).
    """
    cards = []

    if current_user.has_permission("can_view_sod"):
        cards.append({
            "title": "SOD / GRC",
            "description": "Análisis de segregación de funciones SAP y gestión de reglas.",
            "url": "sod.index",
            "stat_type": "sod",
            "stats": get_sod_summary(),
        })

    if current_user.has_permission("can_view_licenses"):
        cards.append({
            "title": "Licencias SAP",
            "description": "Control y seguimiento de licencias SAP asignadas.",
            "url": "licenses.index",
            "stat_type": "licenses",
            "stats": get_license_summary(),
        })

    return render_template("main/dashboard.html", cards=cards)
