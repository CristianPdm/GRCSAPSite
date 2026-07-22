"""Utilidades comunes para leer archivos Excel (.xlsx) exportados de SAP.

Estas funciones reemplazan, en Python, al parseo que el archivo original
(SAP_SOD_Importer_v2.html) hacia en el navegador con la libreria SheetJS.
Las columnas reales de los exports SAP varian (idioma, version, variante de
transaccion), por eso la busqueda de columnas es por substring tolerante en
vez de exigir un nombre exacto -- igual que el helper dc()/fc() del original.
"""
import io
import unicodedata
from datetime import date, datetime

import openpyxl


def _strip_accents(text):
    """Quita tildes/diacriticos (NFKD) para que 'autorización' y
    'autorizacion' coincidan al buscar columnas por substring."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def read_excel_matrix(file_stream, header_search_rows=5):
    """Lee la primera hoja como filas crudas (tuplas), detectando la fila de
    encabezado real entre las primeras `header_search_rows` filas (algunos
    exports SAP traen una fila de titulo antes del encabezado real).

    Devuelve (headers_en_mayusculas_sin_tildes, filas_de_datos_crudas). Se
    trabaja por posicion (no por nombre de columna) para tolerar encabezados
    duplicados o vacios, frecuentes en estos exports.
    """
    # El stream que entrega Werkzeug en la subida de archivos es un
    # SpooledTemporaryFile. En Python < 3.11 ese objeto no tiene el metodo
    # seekable() que openpyxl necesita para leer el .xlsx (zipfile), por lo
    # que se vuelca a un buffer en memoria simple antes de abrirlo.
    if not hasattr(file_stream, "seekable"):
        file_stream = io.BytesIO(file_stream.read())

    wb = openpyxl.load_workbook(file_stream, data_only=True, read_only=True)
    ws = wb.worksheets[0]
    raw = [list(row) for row in ws.iter_rows(values_only=True)]

    # Umbral de 2 celdas (no 3): algunos exports (ej. FUE_Rol.xlsx) traen un
    # encabezado real con solo 2 columnas tituladas (el resto vienen en
    # blanco). Una fila de titulo de reporte real suele tener una sola celda
    # con texto, asi que el umbral de 2 sigue distinguiendo ambos casos.
    header_idx = 0
    for i in range(min(len(raw), header_search_rows)):
        non_empty = [c for c in raw[i] if c is not None and str(c).strip()]
        if len(non_empty) >= 2:
            header_idx = i
            break

    headers = [_strip_accents(str(c or "").strip().upper()) for c in raw[header_idx]]
    data_rows = raw[header_idx + 1:]
    return headers, data_rows


def column_index(headers_upper, candidates):
    """Devuelve el indice de la primera columna cuyo encabezado (ya en
    mayusculas, sin tildes) contenga, como substring, alguno de los
    terminos de `candidates`. Devuelve None si ninguna columna coincide."""
    for i, header in enumerate(headers_upper):
        for term in candidates:
            if _strip_accents(term.upper()) in header:
                return i
    return None


def column_indices(headers_upper, candidates):
    """Igual que `column_index` pero devuelve TODOS los indices que
    coinciden, no solo el primero. Necesario para exports SAP con
    encabezados duplicados (ej. 'Valor de la autorizacion' bajo/alto en
    AGR_1251.xlsx)."""
    found = []
    for i, header in enumerate(headers_upper):
        for term in candidates:
            if _strip_accents(term.upper()) in header:
                found.append(i)
                break
    return found


def parse_excel_date(value):
    """Convierte un valor de celda Excel (datetime/date/str) en date.
    Devuelve None si no se puede interpretar (celda vacia, texto libre)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
