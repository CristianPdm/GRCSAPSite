"""Migracion puntual: crea la tabla nueva para el estado de cuenta SAP por
usuario (USR02.xlsx -> SapUserStatus):

  - sap_user_status  (SapUserStatus)

Igual que migrate_add_fiori_tables.py, es una tabla completamente nueva:
db.create_all() la crea sola, sin tocar ninguna tabla existente ni sus datos.

Ejecutar una sola vez, despues de actualizar el codigo:

    python migrate_add_usr02_table.py

Es seguro ejecutarlo mas de una vez: si la tabla ya existe, no hace nada.
Despues de correrla, importa USR02.xlsx desde la pantalla de importacion de
SOD para ver el estado de bloqueo y el ultimo login en "Usuarios en riesgo".
"""
from sqlalchemy import inspect

from app import create_app
from app.extensions import db


def run_migration():
    app = create_app()

    with app.app_context():
        inspector = inspect(db.engine)
        tablas_existentes = set(inspector.get_table_names())
        tablas_nuevas = {"sap_user_status"}
        faltantes = tablas_nuevas - tablas_existentes

        if not faltantes:
            print("La tabla de estado de usuario SAP ya existe. No hay nada que hacer.")
            return

        db.create_all()
        print(f"Tablas creadas: {', '.join(sorted(faltantes))}")
        print("Importa USR02.xlsx desde la pantalla de importacion de SOD.")


if __name__ == "__main__":
    run_migration()
