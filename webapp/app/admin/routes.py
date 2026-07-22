from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.admin import bp
from app.decorators import permission_required
from app.extensions import db
from app.models import AuditLog, Role, User

PERMISSION_FIELDS = [
    ("can_manage_users", "Gestionar usuarios"),
    ("can_manage_roles", "Gestionar roles"),
    ("can_view_sod", "Ver módulo SOD/GRC"),
    ("can_run_sod_analysis", "Ejecutar análisis SOD"),
    ("can_manage_sod_config", "Administrar reglas/importación SOD"),
    ("can_export_reports", "Exportar reportes"),
    ("can_view_licenses", "Ver licencias SAP"),
    ("can_manage_licenses", "Administrar licencias SAP"),
    ("can_import_sap_data", "Importar datos SAP (SOD y Licencias)"),
    ("can_view_rolesdb", "Ver Roles y Transacciones SAP"),
    ("can_view_chat", "Usar asistente IA"),
    ("can_view_audit_log", "Ver auditoría"),
]


# ---------------------------------------------------------------------------
# Usuarios
# ---------------------------------------------------------------------------

@bp.route("/usuarios")
@login_required
@permission_required("can_manage_users")
def users():
    all_users = User.query.order_by(User.username).all()
    return render_template("admin/users.html", users=all_users)


@bp.route("/usuarios/nuevo", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_users")
def user_new():
    roles = Role.query.order_by(Role.name).all()

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        role_id = request.form.get("role_id")

        if not username or not password or not role_id:
            flash("Usuario, contraseña y rol son obligatorios.", "error")
            return render_template("admin/user_form.html", roles=roles, user=None)

        # Comparacion insensible a mayusculas/minusculas: no se permite crear
        # "Cris" si ya existe "cris" (ahora que el login tampoco distingue).
        if User.query.filter(func.lower(User.username) == username.lower()).first():
            flash("Ya existe un usuario con ese nombre.", "error")
            return render_template("admin/user_form.html", roles=roles, user=None)

        # El mail NO es unico a nivel de base (ver app/models.py): se permite
        # que dos usuarios compartan casilla, pero se avisa con un warning
        # -- no bloquea el alta.
        if email and User.query.filter(func.lower(User.email) == email.lower()).first():
            flash(f"Atención: el mail '{email}' ya está en uso por otro usuario.", "warning")

        new_user = User(username=username, full_name=full_name, email=email or None, role_id=int(role_id))
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        AuditLog.log("usuario_creado", username=current_user.username,
                     details=f"Creó el usuario '{username}' con rol id {role_id}",
                     ip_address=request.remote_addr)

        flash(f"Usuario '{username}' creado correctamente.", "success")
        return redirect(url_for("admin.users"))

    return render_template("admin/user_form.html", roles=roles, user=None)


@bp.route("/usuarios/<int:user_id>/editar", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_users")
def user_edit(user_id):
    target_user = User.query.get_or_404(user_id)
    roles = Role.query.order_by(Role.name).all()

    if request.method == "POST":
        target_user.full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        target_user.role_id = int(request.form.get("role_id"))

        # El mail NO es unico a nivel de base (ver app/models.py): se permite
        # que dos usuarios compartan casilla, pero se avisa con un warning
        # -- no bloquea la edicion.
        if email and User.query.filter(
            func.lower(User.email) == email.lower(), User.id != target_user.id
        ).first():
            flash(f"Atención: el mail '{email}' ya está en uso por otro usuario.", "warning")

        target_user.email = email or None

        new_password = request.form.get("password", "")
        if new_password:
            target_user.set_password(new_password)

        db.session.commit()
        AuditLog.log("usuario_editado", username=current_user.username,
                     details=f"Editó el usuario '{target_user.username}'",
                     ip_address=request.remote_addr)
        flash(f"Usuario '{target_user.username}' actualizado.", "success")
        return redirect(url_for("admin.users"))

    return render_template("admin/user_form.html", roles=roles, user=target_user)


@bp.route("/usuarios/<int:user_id>/alternar-estado", methods=["POST"])
@login_required
@permission_required("can_manage_users")
def user_toggle_active(user_id):
    target_user = User.query.get_or_404(user_id)

    if target_user.id == current_user.id:
        flash("No puedes deshabilitar tu propio usuario.", "error")
        return redirect(url_for("admin.users"))

    target_user.is_active_user = not target_user.is_active_user
    db.session.commit()

    estado = "habilitado" if target_user.is_active_user else "deshabilitado"
    AuditLog.log("usuario_estado_cambiado", username=current_user.username,
                 details=f"Usuario '{target_user.username}' quedó {estado}",
                 ip_address=request.remote_addr)
    flash(f"Usuario '{target_user.username}' {estado}.", "success")
    return redirect(url_for("admin.users"))


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

@bp.route("/roles")
@login_required
@permission_required("can_manage_roles")
def roles():
    all_roles = Role.query.order_by(Role.name).all()
    return render_template("admin/roles.html", roles=all_roles)


@bp.route("/roles/nuevo", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_roles")
def role_new():
    if request.method == "POST":
        return _save_role(None)
    return render_template("admin/role_form.html", role=None, permission_fields=PERMISSION_FIELDS)


@bp.route("/roles/<int:role_id>/editar", methods=["GET", "POST"])
@login_required
@permission_required("can_manage_roles")
def role_edit(role_id):
    target_role = Role.query.get_or_404(role_id)

    if request.method == "POST":
        return _save_role(target_role)

    return render_template("admin/role_form.html", role=target_role, permission_fields=PERMISSION_FIELDS)


def _save_role(target_role):
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()

    if not name:
        flash("El nombre del rol es obligatorio.", "error")
        return redirect(request.referrer or url_for("admin.roles"))

    if target_role is None:
        target_role = Role(name=name)
        db.session.add(target_role)
        action = "rol_creado"
    else:
        action = "rol_editado"

    target_role.name = name
    target_role.description = description

    for field, _label in PERMISSION_FIELDS:
        setattr(target_role, field, request.form.get(field) == "on")

    db.session.commit()
    AuditLog.log(action, username=current_user.username, details=f"Rol '{name}'",
                 ip_address=request.remote_addr)
    flash(f"Rol '{name}' guardado correctamente.", "success")
    return redirect(url_for("admin.roles"))


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------

@bp.route("/auditoria")
@login_required
@permission_required("can_view_audit_log")
def audit_log():
    entries = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(200).all()
    return render_template("admin/audit_log.html", entries=entries)
