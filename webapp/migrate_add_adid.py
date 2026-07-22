"""Migración: crea sap_fiori_app_reg (si no existe) y agrega columna adid.
Uso: cd webapp && python migrate_add_adid.py
"""
from app import create_app
from app.extensions import db
from app.sod import models as _sod_models  # noqa: F401 — registra todos los modelos SOD

from sqlalchemy import inspect, text

app = create_app()

with app.app_context():
    # 1. Crear tablas faltantes (sin tocar las existentes)
    db.create_all()

    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    print(f"Tablas en BD: {[t for t in tables if 'fiori' in t.lower() or 'chip' in t.lower()]}")

    if "sap_fiori_app_reg" not in tables:
        print("ERROR: sap_fiori_app_reg sigue sin existir tras db.create_all().")
        print("  Verificá que SapFioriAppReg esté definido en app/sod/models.py")
    else:
        cols = [c["name"] for c in inspector.get_columns("sap_fiori_app_reg")]
        print(f"Columnas actuales: {cols}")
        if "adid" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE sap_fiori_app_reg ADD COLUMN adid VARCHAR(80) DEFAULT ''"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sap_fiori_app_reg_adid ON sap_fiori_app_reg (adid)"))
            print("OK: columna adid agregada")
        else:
            print("INFO: columna adid ya existe, nada que hacer")
