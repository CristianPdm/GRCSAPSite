"""Migración: agrega can_view_rolesdb y can_view_chat a la tabla roles.

Uso: cd webapp && python migrate_add_rolesdb_chat_perms.py
"""
from app import create_app
from app.extensions import db
from sqlalchemy import inspect, text

app = create_app()

with app.app_context():
    inspector = inspect(db.engine)
    cols = [c["name"] for c in inspector.get_columns("roles")]

    for col, default in [("can_view_rolesdb", 0), ("can_view_chat", 0)]:
        if col not in cols:
            with db.engine.begin() as conn:
                conn.execute(text(
                    f"ALTER TABLE roles ADD COLUMN {col} BOOLEAN NOT NULL DEFAULT {default}"
                ))
            print(f"OK: columna {col} agregada a roles")
        else:
            print(f"INFO: {col} ya existe en roles")

    print("\nMigracion completada.")
    print("Nota: los roles existentes arrancan con ambos permisos en False.")
    print("Habilitarlos desde Configuracion → Roles en el portal.")
