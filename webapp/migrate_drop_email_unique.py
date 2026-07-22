"""Migracion puntual: quita la restriccion UNIQUE de users.email.

Hasta ahora dos usuarios no podian compartir el mismo mail (la base lo
rechazaba con IntegrityError, que rompia la pantalla con un error feo).
Ahora se permite -- en su lugar, app/admin/routes.py avisa con un warning
si el mail ya esta en uso por otro usuario, pero deja guardar igual.

SQLite no soporta "ALTER TABLE ... DROP CONSTRAINT", asi que para quitar
un UNIQUE de una columna hay que recrear la tabla: se crea `users_new` sin
esa restriccion, se copian los datos de `users`, se borra la tabla vieja y
se renombra la nueva a `users`. Ninguna otra tabla tiene una FK hacia
users.id (se uso username/creado_por como texto en auditoria/excepciones),
asi que la operacion es segura.

Ejecutar una sola vez, despues de actualizar el codigo:

    python migrate_drop_email_unique.py

Es seguro ejecutarlo mas de una vez: si la restriccion ya no existe, no
hace nada. Se recomienda hacer una copia de instance/grc_simpa.db antes de
correrla, como con cualquier migracion."""
from sqlalchemy import text

from app import create_app
from app.extensions import db


def run_migration():
    app = create_app()

    with app.app_context():
        with db.engine.connect() as conn:
            row = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'")
            ).fetchone()
        create_sql = (row[0] or "") if row else ""

        if "UNIQUE (email)" not in create_sql.replace("\n", " "):
            print("La tabla 'users' ya no tiene UNIQUE en email. No hay nada que hacer.")
            return

        # Ninguna otra tabla tiene FK hacia users.id (auditoria/excepciones
        # guardan el usuario como texto), asi que recrear la tabla es seguro.
        db.session.execute(
            text(
                """
                CREATE TABLE users_new (
                    id INTEGER NOT NULL,
                    username VARCHAR(80) NOT NULL,
                    full_name VARCHAR(150) NOT NULL,
                    email VARCHAR(150),
                    password_hash VARCHAR(255) NOT NULL,
                    is_active_user BOOLEAN,
                    created_at DATETIME,
                    last_login_at DATETIME,
                    role_id INTEGER NOT NULL,
                    PRIMARY KEY (id),
                    UNIQUE (username),
                    FOREIGN KEY(role_id) REFERENCES roles (id)
                )
                """
            )
        )
        db.session.execute(
            text(
                """
                INSERT INTO users_new
                    (id, username, full_name, email, password_hash, is_active_user,
                     created_at, last_login_at, role_id)
                SELECT id, username, full_name, email, password_hash, is_active_user,
                       created_at, last_login_at, role_id
                FROM users
                """
            )
        )
        db.session.execute(text("DROP TABLE users"))
        db.session.execute(text("ALTER TABLE users_new RENAME TO users"))
        db.session.commit()

        print("Restriccion UNIQUE de users.email eliminada. Los usuarios ya pueden compartir mail.")


if __name__ == "__main__":
    run_migration()
