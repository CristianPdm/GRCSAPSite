"""Diagnóstico: muestra qué columnas detecta el importer de SUI_TM_MM_APP.
Uso:
    cd webapp
    python diag_columns.py ruta/al/SUI_TM_MM_APP.xlsx

Si no se pasa ruta, busca en el directorio actual cualquier archivo que
contenga 'SUI' en el nombre.
"""
import sys
import os
import glob


# ─── Localizar el archivo ─────────────────────────────────────────────────────
if len(sys.argv) > 1:
    xlsx_path = sys.argv[1]
else:
    candidates = glob.glob("**/*SUI*.xlsx", recursive=True) + \
                 glob.glob("**/*sui*.xlsx", recursive=True) + \
                 glob.glob("**/*TM_MM*.xlsx", recursive=True)
    if not candidates:
        print("ERROR: no se encontró ningún archivo SUI*.xlsx")
        print("Uso: python diag_columns.py ruta/al/archivo.xlsx")
        sys.exit(1)
    xlsx_path = candidates[0]
    print(f"Usando: {xlsx_path}")


# ─── Importar helpers del propio proyecto ─────────────────────────────────────
from app import create_app
from app.sod.importers import read_excel_matrix, column_index

app = create_app()

with open(xlsx_path, "rb") as f:
    headers, rows = read_excel_matrix(f)

print(f"\nTotal columnas detectadas: {len(headers)}")
print(f"Total filas: {len(rows)}")
print("\nColumnas (índice → nombre):")
for i, h in enumerate(headers):
    print(f"  [{i:2d}] {h!r}")


# ─── Simular la detección del importer ────────────────────────────────────────
def det(label, candidates):
    idx = column_index(headers, candidates)
    if idx is None:
        val = "— NO DETECTADO —"
    else:
        # Muestra el valor de la primera fila con datos
        sample = next(
            (str(r[idx] or "").strip() for r in rows if idx < len(r) and r[idx]),
            "(vacío)"
        )
        val = f"col {idx} ({headers[idx]!r}) → ej: {sample!r}"
    print(f"  {label:20s}: {val}")


print("\nDetección de columnas por el importer:")
det("app_id_idx", [
    "ID DE APLICACION", "APPID", "APP ID", "APP_ID",
    "APPLICATION ID", "FIORI APP ID", "ID APLICACION",
])
det("tcode_idx", [
    "CODIGO TRANSACCION", "CODIGO DE TRANSACCION",
    "TCODE", "TRANSACTION", "TRANSACTION CODE", "COD.TRANSACCION",
])
det("catid_idx", [
    "ID DE CATALOGO TECNICO", "ID DE CATALOGO", "CATID", "CAT ID",
    "CATALOG ID", "ID CATALOGO", "CATALOGUE ID", "CAT_ID",
])
det("titulo_idx", [
    "TITULO", "TITLE", "DESCRIPCION", "DESCRIPTION",
    "TEXTO", "TEXT", "APP TITLE", "NOMBRE",
])
det("semobj_idx", [
    "OBJETO SEMANTICO", "SEMANTIC OBJECT", "SEMOBJ",
    "SEMANTIC_OBJECT", "OBJETO SEM",
])
det("adid_idx", [
    "ID DE APLICACION", "ADID", "APP DESCRIPTOR", "DESCRIPTOR ID",
    "ID DESCRIPTOR", "ID DE DESCRIPTOR", "AD ID", "APP_ID",
])

# ─── Muestra filas donde tcode = F0797 ────────────────────────────────────────
tcode_idx = column_index(headers, [
    "CODIGO TRANSACCION", "CODIGO DE TRANSACCION",
    "TCODE", "TRANSACTION", "TRANSACTION CODE", "COD.TRANSACCION",
])
catid_idx = column_index(headers, [
    "ID DE CATALOGO TECNICO", "ID DE CATALOGO", "CATID", "CAT ID",
    "CATALOG ID", "ID CATALOGO", "CATALOGUE ID", "CAT_ID",
])

print("\n─── Todos los valores de filas con tcode F0797 ───────────────────────────")
if tcode_idx is None:
    print("  No se detecto columna TCODE, no se puede filtrar.")
else:
    found = [r for r in rows if tcode_idx < len(r) and str(r[tcode_idx] or "").strip().upper() == "F0797"]
    print(f"  Filas encontradas: {len(found)}")
    for fi, r in enumerate(found[:3]):
        print(f"\n  -- Fila {fi+1} --")
        for ci, h in enumerate(headers):
            val = str(r[ci] or "").strip() if ci < len(r) else ""
            if not val or val.lower() in ("false", "true"):
                continue
            is_guid = len(val) == 32 and all(c in "0123456789ABCDEFabcdef" for c in val)
            marker = " <-- GUID!" if is_guid else ""
            print(f"    [{ci:2d}] {h:45s} = {val!r}{marker}")
