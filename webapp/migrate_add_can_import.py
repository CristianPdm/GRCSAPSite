"""Migracion puntual: agrega la columna can_import_sap_data a la tabla
roles. El proyecto no usa Alembic/Flask-Migrate (solo db.create_all(), que
no modifica tablas ya existentes), asi que las columnas nuevas sobre una
base de datos que ya tenia datos se agregan con un script chico como este.

Ejecutar una sola vez, despues de actualizar el codigo:

    python migrate_add_can_import.py

Es seguro ejecutarlo mas de una vez: si la columna ya existe, no hace nada.
"""
from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db


def run_migration():
    app = create_app()

    with app.app_context():
        inspector = inspect(db.engine)
        columns = [col["name"] for col in inspector.get_columns("roles")]

        if "can_import_sap_data" in columns:
            print("La columna 'can_import_sap_data' ya existe. No hay nada que hacer.")
            return

        db.session.execute(
            text("ALTER TABLE roles ADD COLUMN can_import_sap_data BOOLEAN DEFAULT 0")
        )
        db.session.commit()
        print("Columna 'can_import_sap_data' agregada a la tabla roles.")
        print("Ahora podes ejecutar 'python seed.py' para crear el rol base 'Importador SAP'.")


if __name__ == "__main__":
    run_migration()
