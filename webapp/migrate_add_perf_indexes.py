"""Migracion: indices de rendimiento para filtros SQL frecuentes.

Crea dos indices que no existian en la BD original:
  - ix_sap_role_assignments_valid_to  → build_maps() filtra por esta columna
  - ix_sod_exceptions_regla_id        → FK no indexada automaticamente en SQLite

Ejecutar una sola vez, luego de reiniciar la app con el nuevo codigo.
El script es idempotente: usa CREATE INDEX IF NOT EXISTS.
"""
import sys
import os

# Permite ejecutarlo desde la raiz del proyecto sin tocar PYTHONPATH
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from app.extensions import db


def run():
    app = create_app()
    with app.app_context():
        with db.engine.connect() as conn:
            conn.execute(db.text(
                "CREATE INDEX IF NOT EXISTS ix_sap_role_assignments_valid_to "
                "ON sap_role_assignments (valid_to)"
            ))
            conn.execute(db.text(
                "CREATE INDEX IF NOT EXISTS ix_sod_exceptions_regla_id "
                "ON sod_exceptions (regla_id)"
            ))
            conn.commit()
        print("OK: indices de rendimiento creados (o ya existian).")


if __name__ == "__main__":
    run()
