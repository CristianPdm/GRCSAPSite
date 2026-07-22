"""Migracion puntual: crea las 3 tablas nuevas para resolver apps Fiori a
tcode y sumarlas al motor SOD (AGR_HIER -> AGR_BUFFI -> PB_C_CHIPM):

  - sap_role_hier_nodes  (SapRoleHierNode)
  - sap_buffi_urls       (SapBuffiUrl)
  - sap_fiori_id_tcodes  (SapFioriIdTcode)

A diferencia de migrate_add_parent_role.py / migrate_add_can_import.py (que
agregan una COLUMNA a una tabla ya existente y por eso necesitan un ALTER
TABLE manual), estas son tablas completamente nuevas: db.create_all() las
crea solo, sin tocar ninguna tabla existente ni sus datos.

Ejecutar una sola vez, despues de actualizar el codigo:

    python migrate_add_fiori_tables.py

Es seguro ejecutarlo mas de una vez: si las tablas ya existen, no hace nada.
Despues de correrla, importa AGR_HIER.xlsx, AGR_BUFFI.xlsx y PB_C_CHIPM
(.xlsx o .csv) desde la pantalla de importacion de SOD para que se calculen
los tcodes Fiori por rol.
"""
from sqlalchemy import inspect

from app import create_app
from app.extensions import db


def run_migration():
    app = create_app()

    with app.app_context():
        inspector = inspect(db.engine)
        tablas_existentes = set(inspector.get_table_names())
        tablas_nuevas = {"sap_role_hier_nodes", "sap_buffi_urls", "sap_fiori_id_tcodes"}
        faltantes = tablas_nuevas - tablas_existentes

        if not faltantes:
            print("Las tablas de apps Fiori ya existen. No hay nada que hacer.")
            return

        db.create_all()
        print(f"Tablas creadas: {', '.join(sorted(faltantes))}")
        print("Importa AGR_HIER.xlsx, AGR_BUFFI.xlsx y PB_C_CHIPM (.xlsx o .csv) "
              "desde la pantalla de importacion de SOD.")


if __name__ == "__main__":
    run_migration()
