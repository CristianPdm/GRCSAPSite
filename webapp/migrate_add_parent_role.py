"""Migracion puntual: agrega la columna parent_role a la tabla
sap_role_descriptions. El proyecto no usa Alembic/Flask-Migrate (solo
db.create_all(), que no modifica tablas ya existentes), asi que las
columnas nuevas sobre una base de datos que ya tenia datos se agregan con
un script chico como este.

Esta columna guarda el rol "padre" (compuesto) de AGR_DEFINE, necesaria
para que el motor SOD expanda roles compuestos a sus roles simples.

Ejecutar una sola vez, despues de actualizar el codigo:

    python migrate_add_parent_role.py

Es seguro ejecutarlo mas de una vez: si la columna ya existe, no hace nada.
Despues de correrla, hay que reimportar AGR_DEFINE.xlsx para que se llene
el dato (las filas ya importadas quedan con parent_role en blanco).
"""
from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db


def run_migration():
    app = create_app()

    with app.app_context():
        inspector = inspect(db.engine)
        columns = [col["name"] for col in inspector.get_columns("sap_role_descriptions")]

        if "parent_role" in columns:
            print("La columna 'parent_role' ya existe. No hay nada que hacer.")
            return

        db.session.execute(
            text("ALTER TABLE sap_role_descriptions ADD COLUMN parent_role VARCHAR(80)")
        )
        db.session.commit()
        print("Columna 'parent_role' agregada a la tabla sap_role_descriptions.")
        print("Reimporta AGR_DEFINE.xlsx para que se cargue el rol padre de cada rol.")


if __name__ == "__main__":
    run_migration()
