"""Modelos de datos del modulo de Licencias SAP (FUE).

Reemplazan, con columnas tipadas, al esquema generico del original
(SAP_SOD_Analyzer_v640.html), que guardaba FUE_Rol.xlsx y FUE_Users.xlsx
como filas crudas en memoria sin persistirlas en SQLite.
"""
from datetime import datetime

from app.extensions import db


class LicenseRole(db.Model):
    """Tipo FUE asignado a nivel de ROL, importado de FUE_Rol.xlsx.

    Util como referencia / auditoria de catalogo, aunque el calculo oficial
    de licenciamiento (canonico) se hace por usuario via LicenseUser."""

    __tablename__ = "license_roles"

    id = db.Column(db.Integer, primary_key=True)
    role_name = db.Column(db.String(80), nullable=False, index=True)
    description = db.Column(db.String(255), default="")
    fue_type_raw = db.Column(db.String(80), default="")   # texto crudo, ej. 'GD Self-Service Use'
    fue_type_code = db.Column(db.String(10), default="NONE")  # ADV | CORE | SELF | NONE
    ratio = db.Column(db.String(20), default="")           # ej. '1/1'
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)


class LicenseUser(db.Model):
    """Tipo FUE oficial asignado a nivel de USUARIO, importado de
    FUE_Users.xlsx. Es la fuente canonica para el calculo de licencias
    (ver app/licenses/rules.py); el tipo a nivel de rol queda como
    referencia secundaria."""

    __tablename__ = "license_users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), nullable=False, unique=True, index=True)
    full_name = db.Column(db.String(150), default="")
    fue_type_raw = db.Column(db.String(80), default="")    # texto crudo, ej. 'GC Core Use'
    fue_type_code = db.Column(db.String(10), default="NONE")  # ADV | CORE | SELF | NONE
    indice_fue = db.Column(db.String(20), default="")      # ej. '17/41'
    last_access = db.Column(db.Date, nullable=True)         # 'UltimoAcceso'
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)
