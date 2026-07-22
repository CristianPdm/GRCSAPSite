"""Migración: agrega columnas page_ref_catalog (sap_chip_catalog)
y catid (sap_fiori_app_reg) necesarias para resolución de referencias
PAGE de PB_C_CHIPM y herencia de catálogos.

Uso: cd webapp && python migrate_add_page_ref_catid.py
"""
from app import create_app
from app.extensions import db
from app.sod import models as _sod_models  # noqa: F401
from sqlalchemy import inspect, text

app = create_app()

with app.app_context():
    db.create_all()
    inspector = inspect(db.engine)

    # ── sap_chip_catalog: columna page_ref_catalog ─────────────────────────
    cols_chip = [c["name"] for c in inspector.get_columns("sap_chip_catalog")]
    if "page_ref_catalog" not in cols_chip:
        with db.engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE sap_chip_catalog ADD COLUMN page_ref_catalog VARCHAR(120) DEFAULT ''"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_sap_chip_catalog_page_ref_catalog "
                "ON sap_chip_catalog (page_ref_catalog)"
            ))
        print("OK: columna page_ref_catalog agregada a sap_chip_catalog")
    else:
        print("INFO: page_ref_catalog ya existe en sap_chip_catalog")

    # ── sap_fiori_app_reg: columna catid ───────────────────────────────────
    cols_reg = [c["name"] for c in inspector.get_columns("sap_fiori_app_reg")]
    if "catid" not in cols_reg:
        with db.engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE sap_fiori_app_reg ADD COLUMN catid VARCHAR(120) DEFAULT ''"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_sap_fiori_app_reg_catid "
                "ON sap_fiori_app_reg (catid)"
            ))
        print("OK: columna catid agregada a sap_fiori_app_reg")
    else:
        print("INFO: catid ya existe en sap_fiori_app_reg")

    print("\nMigracion completada.")
    print("Proximos pasos:")
    print("  1. Reimportar PB_C_CHIPM  (captura page_ref_catalog)")
    print("  2. Reimportar SUI_TM_MM_APP (captura catid)")
    print("  → F0797 debería aparecer en ZSD_FACTURAS al buscar en RolesDB")
