"""Importacion en lote de archivos SAP desde una carpeta configurable.

En lugar de subir cada tabla de a una (AGR_USERS.xlsx, AGR_1251.xlsx, etc.),
un administrador puede dejar todos los Excel exportados de SAP en una misma
carpeta del servidor y disparar un escaneo: este modulo busca, para cada
nombre de archivo esperado, un .xlsx con ese nombre (sin distinguir
mayusculas/minusculas) y ejecuta el importer correspondiente.

La carpeta en si se guarda con app.models.AppSetting (clave
SAP_IMPORT_FOLDER_SETTING), editable desde la pantalla de importacion de
SOD o de Licencias (es una sola carpeta compartida por ambos modulos).
"""
import os
from datetime import timedelta

from flask import current_app

from app.extensions import db

SAP_IMPORT_FOLDER_SETTING = "sap_import_folder"


def last_import_date(*models):
    """Fecha/hora (UTC, sin convertir) de la importacion mas reciente entre
    los modelos dados.

    Todos los modelos de datos importados de SAP/Excel tienen un campo
    `imported_at` (ver app/sod/models.py y app/licenses/models.py). Se usa
    para mostrar, en las pantallas que muestran 'ultimo acceso'/'ultimo
    login' de un usuario, cuando se actualizo por ultima vez la tabla de
    origen de ese dato -- sin eso, una fecha de acceso vieja se puede leer
    como inactividad real cuando en realidad solo es que nadie volvio a
    importar el reporte. Devuelve None si ninguno de los modelos tiene
    filas importadas todavia.

    El valor devuelto esta en UTC (asi se guarda con datetime.utcnow() en
    toda la app): para mostrarlo en pantalla hay que pasarlo por el filtro
    de plantilla `local_dt` (ver to_local mas abajo), que lo convierte a la
    hora local de APP_TIMEZONE_OFFSET_HOURS antes de aplicar strftime."""
    fechas = [
        db.session.query(db.func.max(modelo.imported_at)).scalar()
        for modelo in models
    ]
    fechas = [f for f in fechas if f]
    return max(fechas) if fechas else None


def to_local(dt):
    """Convierte un datetime naive en UTC (el formato en que se guarda todo
    en esta app, via datetime.utcnow()) a la hora local de Grupo Simpa,
    sumando el offset fijo APP_TIMEZONE_OFFSET_HOURS (config.py), para
    mostrarlo correctamente.

    Se usa un offset fijo (en horas) en lugar del modulo `zoneinfo` con un
    nombre de zona IANA porque ese modulo depende de que el sistema (o el
    paquete `tzdata`) tenga instalada la base de datos de zonas horarias, lo
    cual no esta garantizado en todos los servidores; un offset fijo no
    tiene esa dependencia y, como Uruguay/Argentina no aplican horario de
    verano actualmente, no se pierde precision.

    Sin esta conversion, una importacion hecha de noche en Uruguay/Argentina
    (UTC-3) puede aparecer fechada al dia siguiente en pantalla, porque
    00:00 hora local ya es 03:00 UTC. Se registra como filtro de Jinja
    ("local_dt") en app/__init__.py y se usa en las plantillas que muestran
    `imported_at` con hora (ej. el aviso de ultima actualizacion de datos)."""
    if dt is None:
        return None
    offset_hours = current_app.config.get("APP_TIMEZONE_OFFSET_HOURS", -3)
    return dt + timedelta(hours=offset_hours)


def scan_import_folder(folder, importers):
    """Escanea `folder` buscando un .xlsx por cada clave de `importers` y
    ejecuta el importer correspondiente sobre el primero que encuentre.

    `importers` es un dict {tipo: (label, importer_func)}, igual al que ya
    usan las rutas de importacion manual. `importer_func` recibe un stream
    de archivo abierto en modo binario.

    Devuelve una lista de resultados (uno por cada tipo esperado, en el
    mismo orden que `importers`), o None si la carpeta no existe o no se
    puede leer. Cada resultado es un dict con:
      - tipo, label: igual que la entrada de `importers`
      - found: True/False, si se encontro un archivo con ese nombre
      - ok: True si se encontro y se importo sin errores
      - count: filas importadas (solo si ok)
      - filename: nombre real del archivo encontrado (si found)
      - error: mensaje de error (si found y no ok)
    """
    if not folder or not os.path.isdir(folder):
        return None

    try:
        entries = os.listdir(folder)
    except OSError:
        return None

    # Mapea NOMBRE_SIN_EXTENSION (mayusculas) -> ruta completa del primer
    # .xlsx encontrado con ese nombre.
    found_by_stem = {}
    for entry in entries:
        full_path = os.path.join(folder, entry)
        if not os.path.isfile(full_path):
            continue
        stem, ext = os.path.splitext(entry)
        if ext.lower() != ".xlsx":
            continue
        stem_key = stem.strip().upper()
        if stem_key not in found_by_stem:
            found_by_stem[stem_key] = full_path

    results = []
    for tipo, (label, importer_func) in importers.items():
        path = found_by_stem.get(tipo.upper())

        if not path:
            results.append({
                "tipo": tipo, "label": label, "found": False, "ok": False,
            })
            continue

        filename = os.path.basename(path)
        try:
            with open(path, "rb") as stream:
                count = importer_func(stream)
            results.append({
                "tipo": tipo, "label": label, "found": True, "ok": True,
                "count": count, "filename": filename,
            })
        except Exception as exc:
            results.append({
                "tipo": tipo, "label": label, "found": True, "ok": False,
                "error": str(exc), "filename": filename,
            })

    return results
