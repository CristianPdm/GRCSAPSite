from datetime import datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func

from app.auth import bp
from app.extensions import db
from app.models import AuditLog, User


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        # Login insensible a mayusculas/minusculas: "Cris", "cris" y "CRIS"
        # deben loguear al mismo usuario.
        user = User.query.filter(func.lower(User.username) == username.lower()).first()

        if user is None or not user.check_password(password):
            AuditLog.log("login_fallido", username=username, ip_address=request.remote_addr)
            flash("Usuario o contraseña incorrectos.", "error")
            return render_template("auth/login.html")

        if not user.is_active_user:
            AuditLog.log("login_bloqueado", username=user.username, ip_address=request.remote_addr,
                         details="Usuario deshabilitado")
            flash("Tu cuenta está deshabilitada. Contacta al administrador.", "error")
            return render_template("auth/login.html")

        login_user(user)
        user.last_login_at = datetime.utcnow()
        db.session.commit()
        AuditLog.log("login_exitoso", username=user.username, ip_address=request.remote_addr)

        next_page = request.args.get("next")
        return redirect(next_page or url_for("main.dashboard"))

    return render_template("auth/login.html")


@bp.route("/logout")
@login_required
def logout():
    AuditLog.log("logout", username=current_user.username, ip_address=request.remote_addr)
    logout_user()
    flash("Sesion cerrada correctamente.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/cambiar-contrasena", methods=["GET", "POST"])
@login_required
def cambiar_contrasena():
    if request.method == "POST":
        actual = request.form.get("actual", "")
        nueva = request.form.get("nueva", "")
        confirmar = request.form.get("confirmar", "")

        if not current_user.check_password(actual):
            flash("La contrasena actual es incorrecta.", "error")
            return render_template("auth/cambiar_contrasena.html")

        if len(nueva) < 6:
            flash("La nueva contrasena debe tener al menos 6 caracteres.", "error")
            return render_template("auth/cambiar_contrasena.html")

        if nueva != confirmar:
            flash("La nueva contrasena y la confirmacion no coinciden.", "error")
            return render_template("auth/cambiar_contrasena.html")

        current_user.set_password(nueva)
        db.session.commit()
        AuditLog.log(
            "cambio_contrasena",
            username=current_user.username,
            ip_address=request.remote_addr,
        )
        flash("Contrasena actualizada correctamente.", "success")
        return redirect(url_for("main.dashboard"))

    return render_template("auth/cambiar_contrasena.html")
