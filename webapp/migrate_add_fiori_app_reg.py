"""Migración: crea las tablas sap_chip_catalog y sap_fiori_app_reg.

  sap_chip_catalog  — staging de PB_C_CHIPM con component_id preservado
  sap_fiori_app_reg — registro de apps Fiori de SUI_TM_MM_APP

Idempotente: usa CREATE TABLE IF NOT EXISTS.
Ejecutar una sola vez después de actualizar el código a esta versión:
    python3 migrate_add_fiori_app_reg.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from app.extensions import db

app = create_app()

with app.app_context():
    with db.engine.connect() as conn:
        conn.execute(db.text("""
            CREATE TABLE IF NOT EXISTS sap_chip_catalog (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                catalog_or_group_id TEXT NOT NULL,
                component_id  TEXT NOT NULL DEFAULT '',
                config_raw    TEXT NOT NULL DEFAULT '',
                imported_at   DATETIME
            )
        """))
        conn.execute(db.text(
            "CREATE INDEX IF NOT EXISTS ix_sap_chip_catalog_catalog "
            "ON sap_chip_catalog (catalog_or_group_id)"
        ))
        conn.execute(db.text(
            "CREATE INDEX IF NOT EXISTS ix_sap_chip_catalog_component "
            "ON sap_chip_catalog (component_id)"
        ))

        conn.execute(db.text("""
            CREATE TABLE IF NOT EXISTS sap_fiori_app_reg (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id           TEXT NOT NULL,
                tcode            TEXT NOT NULL,
                titulo           TEXT NOT NULL DEFAULT '',
                semantic_object  TEXT NOT NULL DEFAULT '',
                imported_at      DATETIME,
                CONSTRAINT uq_fiori_app_reg UNIQUE (app_id, tcode)
            )
        """))
        conn.execute(db.text(
            "CREATE INDEX IF NOT EXISTS ix_sap_fiori_app_reg_app_id "
            "ON sap_fiori_app_reg (app_id)"
        ))
        conn.commit()

    print("✓ Tablas sap_chip_catalog y sap_fiori_app_reg creadas/verificadas.")
    print("  Podés importar SUI_TM_MM_APP desde la pantalla de importación SOD.")
