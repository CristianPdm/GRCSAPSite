import os

from flask import Flask, render_template

from config import BASE_DIR, Config
from app.extensions import db, login_manager


def create_app(config_class=Config):
    # templates/ y static/ viven en la raiz de webapp/, no dentro de app/,
    # por eso se indican explicitamente (si no, Flask buscaria app/templates).
    app = Flask(
        __name__,
        instance_relative_config=True,
        template_folder=os.path.join(BASE_DIR, "templates"),
        static_folder=os.path.join(BASE_DIR, "static"),
    )
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # --- Registro de blueprints ---
    from app.auth import bp as auth_bp
    from app.main import bp as main_bp
    from app.admin import bp as admin_bp
    from app.sod import bp as sod_bp
    from app.licenses import bp as licenses_bp
    from app.rolesdb import bp as rolesdb_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(sod_bp, url_prefix="/sod")
    app.register_blueprint(licenses_bp, url_prefix="/licencias")
    app.register_blueprint(rolesdb_bp)

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/404.html"), 404

    @app.context_processor
    def inject_app_info():
        return {"app_name": app.config["APP_NAME"], "app_version": app.config["APP_VERSION"]}

    # Filtro de plantilla para mostrar fechas guardadas en UTC (imported_at,
    # etc.) en la zona horaria local configurada en APP_TIMEZONE.
    from app.utils.sap_import import to_local
    app.jinja_env.filters["local_dt"] = to_local

    return app
