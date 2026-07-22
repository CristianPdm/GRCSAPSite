"""Importadores de archivos Excel SAP (FUE_*) hacia las tablas tipadas del
modulo de Licencias. FUE_Rol.xlsx y FUE_Users.xlsx traen varias columnas sin
encabezado (blancas) en este export especifico, por lo que se accede por
posicion fija en vez de por nombre -- se valida el nombre de las primeras
columnas (que si tienen encabezado) para detectar el archivo equivocado.
"""
from app.extensions import db
from app.utils.excel import column_index, parse_excel_date, read_excel_matrix
from app.licenses.models import LicenseRole, LicenseUser
from app.licenses.rules import classify_fue_type
from app.sod.engine import invalidate_analysis_cache

# Posiciones fijas (0-based) confirmadas inspeccionando los archivos reales.
FUE_ROL_COL_ROL = 0
FUE_ROL_COL_DESC = 1
FUE_ROL_COL_TIPO = 2
FUE_ROL_COL_RATIO = 3

FUE_USERS_COL_USER = 0
FUE_USERS_COL_NOMBRE = 1
FUE_USERS_COL_TIPO = 4
FUE_USERS_COL_INDICE = 5
FUE_USERS_COL_ULTIMO_ACCESO = 8


def import_fue_roles(file_stream):
    """FUE_Rol.xlsx: tipo FUE asignado a nivel de rol (referencia, no es el
    calculo oficial de licencias)."""
    headers, rows = read_excel_matrix(file_stream)

    if column_index(headers, ["ROL"]) != FUE_ROL_COL_ROL:
        raise ValueError("FUE_Rol.xlsx: la columna 'Rol' no esta en la posicion esperada.")

    LicenseRole.query.delete()

    count = 0
    for row in rows:
        if FUE_ROL_COL_ROL >= len(row):
            continue
        role_name = row[FUE_ROL_COL_ROL]
        if not role_name:
            continue

        description = ""
        if FUE_ROL_COL_DESC < len(row) and row[FUE_ROL_COL_DESC]:
            description = str(row[FUE_ROL_COL_DESC]).strip()

        fue_raw = ""
        if FUE_ROL_COL_TIPO < len(row) and row[FUE_ROL_COL_TIPO]:
            fue_raw = str(row[FUE_ROL_COL_TIPO]).strip()

        ratio = ""
        if FUE_ROL_COL_RATIO < len(row) and row[FUE_ROL_COL_RATIO]:
            ratio = str(row[FUE_ROL_COL_RATIO]).strip()

        db.session.add(LicenseRole(
            role_name=str(role_name).strip(),
            description=description,
            fue_type_raw=fue_raw,
            fue_type_code=classify_fue_type(fue_raw),
            ratio=ratio,
        ))
        count += 1

    db.session.commit()
    invalidate_analysis_cache()
    return count


def import_fue_users(file_stream):
    """FUE_Users.xlsx: tipo FUE oficial asignado a nivel de usuario -- es la
    fuente canonica para el calculo de licencias (ver app/licenses/rules.py
    y app/licenses/engine.py)."""
    headers, rows = read_excel_matrix(file_stream)

    if column_index(headers, ["USER"]) != FUE_USERS_COL_USER:
        raise ValueError("FUE_Users.xlsx: la columna 'USER' no esta en la posicion esperada.")

    LicenseUser.query.delete()

    count = 0
    seen = set()
    for row in rows:
        if FUE_USERS_COL_USER >= len(row):
            continue
        username = row[FUE_USERS_COL_USER]
        if not username:
            continue
        username = str(username).strip()
        if username in seen:
            continue
        seen.add(username)

        full_name = ""
        if FUE_USERS_COL_NOMBRE < len(row) and row[FUE_USERS_COL_NOMBRE]:
            full_name = str(row[FUE_USERS_COL_NOMBRE]).strip()

        fue_raw = ""
        if FUE_USERS_COL_TIPO < len(row) and row[FUE_USERS_COL_TIPO]:
            fue_raw = str(row[FUE_USERS_COL_TIPO]).strip()

        indice_fue = ""
        if FUE_USERS_COL_INDICE < len(row) and row[FUE_USERS_COL_INDICE]:
            indice_fue = str(row[FUE_USERS_COL_INDICE]).strip()

        last_access = None
        if FUE_USERS_COL_ULTIMO_ACCESO < len(row):
            last_access = parse_excel_date(row[FUE_USERS_COL_ULTIMO_ACCESO])

        db.session.add(LicenseUser(
            username=username,
            full_name=full_name,
            fue_type_raw=fue_raw,
            fue_type_code=classify_fue_type(fue_raw),
            indice_fue=indice_fue,
            last_access=last_access,
        ))
        count += 1

    db.session.commit()
    invalidate_analysis_cache()
    return count
