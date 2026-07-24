"""Migracion puntual: crea las tablas nuevas para niveles organizacionales
por rol (AGR_1252.xlsx -> SapRoleOrgLevel, USVAR.xlsx -> SapOrgLevelDesc):

  - sap_role_org_levels        (SapRoleOrgLevel)
  - sap_org_level_descriptions (SapOrgLevelDesc)

Igual que migrate_add_usr02_table.py, son tablas completamente nuevas:
db.create_all() las crea solas, sin tocar ninguna tabla existente ni sus datos.

Ejecutar una sola vez, despues de actualizar el codigo:

    python migrate_add_org_levels.py

Es seguro ejecutarlo mas de una vez: si las tablas ya existen, no hace nada.
Despues de correrla, importa AGR_1252.xlsx (obligatorio) y USVAR.xlsx
(opcional, solo agrega texto descriptivo) desde la pantalla de importacion
de SOD para ver el nivel organizacional en el detalle de rol (modulo Roles
y Transacciones).
"""
from sqlalchemy import inspect

from app import create_app
from app.extensions import db


def run_migration():
    app = create_app()

    with app.app_context():
        inspector = inspect(db.engine)
        tablas_existentes = set(inspector.get_table_names())
        tablas_nuevas = {"sap_role_org_levels", "sap_org_level_descriptions"}
        faltantes = tablas_nuevas - tablas_existentes

        if not faltantes:
            print("Las tablas de nivel organizacional ya existen. No hay nada que hacer.")
            return

        db.create_all()
        print(f"Tablas creadas: {', '.join(sorted(faltantes))}")
        print("Importa AGR_1252.xlsx (y opcionalmente USVAR.xlsx) desde la pantalla de importacion de SOD.")


if __name__ == "__main__":
    run_migration()
