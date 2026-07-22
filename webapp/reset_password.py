"""reset_password.py — Blanqueo de contrasena desde la linea de comandos.

Uso:
    python reset_password.py <usuario>
    python reset_password.py <usuario> <nueva_contrasena>

Si no se pasa la nueva contrasena, se solicita de forma interactiva
(sin eco en pantalla). Util cuando un usuario queda bloqueado sin acceso
al sitio o cuando el administrador del servidor necesita restablecer
el acceso de emergencia.

Debe ejecutarse desde la carpeta webapp/ con el entorno virtual activo:
    cd webapp
    python reset_password.py admin
"""

import sys
import os


def main():
    if len(sys.argv) < 2:
        print("Uso: python reset_password.py <usuario> [nueva_contrasena]")
        sys.exit(1)

    username = sys.argv[1].strip()

    if len(sys.argv) >= 3:
        nueva = sys.argv[2]
    else:
        import getpass
        nueva = getpass.getpass(f"Nueva contrasena para '{username}': ")
        confirmar = getpass.getpass("Confirmar contrasena: ")
        if nueva != confirmar:
            print("ERROR: Las contrasenas no coinciden.")
            sys.exit(1)

    if len(nueva) < 6:
        print("ERROR: La contrasena debe tener al menos 6 caracteres.")
        sys.exit(1)

    # Cargar la app Flask para acceder a la base de datos
    from app import create_app
    from app.extensions import db
    from app.models import User, AuditLog
    from sqlalchemy import func

    app = create_app()
    with app.app_context():
        user = User.query.filter(
            func.lower(User.username) == username.lower()
        ).first()

        if user is None:
            print(f"ERROR: No se encontro el usuario '{username}'.")
            sys.exit(1)

        user.set_password(nueva)

        # Reactivar si estaba deshabilitado
        if not user.is_active_user:
            user.is_active_user = True
            print(f"INFO: El usuario '{user.username}' estaba deshabilitado. Se reactivo.")

        db.session.commit()

        AuditLog.log(
            "reset_contrasena_cli",
            username=user.username,
            ip_address="CLI",
            details="Contrasena restablecida desde linea de comandos",
        )
        db.session.commit()

        print(f"OK: Contrasena de '{user.username}' actualizada correctamente.")


if __name__ == "__main__":
    main()
