from functools import wraps

from flask import abort
from flask_login import current_user


def permission_required(*permission_names):
    """Decorador para proteger una vista segun el permiso del rol del usuario.

    Si se pasa un solo permiso, funciona igual que antes (debe tenerlo).
    Si se pasan varios, alcanza con tener UNO cualquiera de ellos (OR) -
    util para acciones que pueden habilitarse por mas de un permiso, por
    ejemplo la importacion de datos SAP (can_manage_sod_config O
    can_import_sap_data).

    Para exigir varios permisos a la vez (AND), se siguen apilando
    decoradores como antes:
        @permission_required("can_export_reports")
        @permission_required("can_view_sod")

    Uso:
        @app.route("/admin/usuarios")
        @login_required
        @permission_required("can_manage_users")
        def lista_usuarios():
            ...
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated or not any(
                current_user.has_permission(name) for name in permission_names
            ):
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator
