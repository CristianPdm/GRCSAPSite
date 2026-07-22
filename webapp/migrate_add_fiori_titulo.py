"""Migracion puntual: agrega la columna titulo a la tabla
sap_fiori_id_tcodes. El proyecto no usa Alembic/Flask-Migrate (solo
db.create_all(), que no modifica tablas ya existentes), asi que las
columnas nuevas sobre una base de datos que ya tenia datos se agregan con
un script chico como este.

Esta columna guarda el 'display_title_text' del tile/chip Fiori (titulo
visible en el Launchpad), extraido de la columna CONFIGURATION de
PB_C_CHIPM.xlsx. Se usa solo como respaldo de descripcion en el picker de
TCodes (pantalla de reglas SOD) cuando TSTCT.xlsx no cubre ese codigo --
las apps Fiori puras (OData, sin tcode) siguen sin description alguna.

Ejecutar una sola vez, despues de actualizar el codigo:

    python migrate_add_fiori_titulo.py

Es seguro ejecutarlo mas de una vez: si la columna ya existe, no hace nada.
Despues de correrla, hay que reimportar PB_C_CHIPM.xlsx para que se llene
el dato (las filas ya importadas quedan con titulo en blanco)."""
from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db


def run_migration():
    app = create_app()

    with app.app_context():
        inspector = inspect(db.engine)
        columns = [col["name"] for col in inspector.get_columns("sap_fiori_id_tcodes")]

        if "titulo" in columns:
            print("La columna 'titulo' ya existe. No hay nada que hacer.")
            return

        db.session.execute(
            text("ALTER TABLE sap_fiori_id_tcodes ADD COLUMN titulo VARCHAR(120) DEFAULT ''")
        )
        db.session.commit()
        print("Columna 'titulo' agregada a la tabla sap_fiori_id_tcodes.")
        print("Reimporta PB_C_CHIPM.xlsx para que se cargue el titulo de cada chip Fiori.")


if __name__ == "__main__":
    run_migration()
