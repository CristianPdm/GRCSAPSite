"""Importadores de archivos Excel SAP (AGR_*) hacia las tablas tipadas del
modulo SOD. Reemplazan el parseo en navegador (SheetJS) de
SAP_SOD_Importer_v2.html.

Cada importer borra los datos previos de su propia tabla y vuelve a
cargarlos completos (import "full refresh"), igual que el original.
"""
import csv
import io
import json
import re
from collections import defaultdict

from app.extensions import db
from app.utils.excel import _strip_accents, column_index, column_indices, parse_excel_date, read_excel_matrix
from app.sod.engine import invalidate_analysis_cache

from app.sod.models import (
    SapBuffiUrl,
    SapChipCatalog,
    SapFioriAppReg,
    SapFioriIdTcode,
    SapOrgLevelDesc,
    SapRoleAssignment,
    SapRoleDescription,
    SapRoleHierNode,
    SapRoleOrgLevel,
    SapRoleTableAuth,
    SapRoleTcode,
    SapTcodeDescription,
    SapUserStatus,
)


def import_agr_users(file_stream):
    """AGR_USERS.xlsx: asignacion Rol -> Usuario, con vigencia y exclusion.
    Columnas reales: Rol(0), Usuarios(1), Fecha de inicio(2), Fecha fin(3),
    Excluido(4)."""
    headers, rows = read_excel_matrix(file_stream)

    role_idx = column_index(headers, ["ROL"])
    user_idx = column_index(headers, ["USUARIO"])
    fecha_fin_idx = column_index(headers, ["FECHA FIN"])
    excluido_idx = column_index(headers, ["EXCLUIDO"])

    if role_idx is None or user_idx is None:
        raise ValueError("AGR_USERS.xlsx: no se encontraron las columnas Rol/Usuario.")

    SapRoleAssignment.query.delete()

    count = 0
    for row in rows:
        if role_idx >= len(row) or user_idx >= len(row):
            continue
        role_name = row[role_idx]
        username = row[user_idx]
        if not role_name or not username:
            continue

        if excluido_idx is not None and excluido_idx < len(row):
            excluido_val = row[excluido_idx]
            if excluido_val and str(excluido_val).strip().upper() in ("X", "SI", "TRUE", "1"):
                continue

        valid_to = None
        if fecha_fin_idx is not None and fecha_fin_idx < len(row):
            valid_to = parse_excel_date(row[fecha_fin_idx])

        db.session.add(SapRoleAssignment(
            role_name=str(role_name).strip(),
            username=str(username).strip(),
            valid_to=valid_to,
        ))
        count += 1

    db.session.commit()
    invalidate_analysis_cache()
    return count


def import_agr_1251(file_stream):
    """AGR_1251.xlsx: transacciones (S_TCODE/TCD) habilitadas por cada rol,
    y de paso los roles con autorizacion irrestricta a tablas (objeto
    S_TABU_DIS, valor '*' -> bandera de riesgo en Roles criticos).
    Columnas reales: Rol(0), ID(1), Obj.autorizacion(2),
    Actual.maestro usuario...(3), Variante(4), Nombre campo(5),
    Valor de la autorizacion BAJO(6), Valor de la autorizacion ALTO(7,
    encabezado duplicado), Status del objeto(8)."""
    headers, rows = read_excel_matrix(file_stream)

    role_idx = column_index(headers, ["ROL"])
    obj_auth_idx = column_index(headers, ["OBJ.AUTORIZACION", "OBJ AUTORIZACION", "OBJETO DE AUTORIZACION"])
    field_name_idx = column_index(headers, ["NOMBRE CAMPO", "NOMBRE DE CAMPO"])
    valor_indices = column_indices(headers, ["VALOR DE LA AUTORIZACION", "VALOR AUTORIZACION"])

    if role_idx is None or obj_auth_idx is None or field_name_idx is None or not valor_indices:
        raise ValueError("AGR_1251.xlsx: estructura de columnas inesperada (Rol/Obj.autorizacion/Nombre campo/Valor).")

    SapRoleTcode.query.filter_by(source="AGR_1251").delete()
    SapRoleTableAuth.query.delete()

    count = 0
    seen = set()
    tabu_dis_roles = set()
    for row in rows:
        if role_idx >= len(row) or obj_auth_idx >= len(row) or field_name_idx >= len(row):
            continue
        obj_auth = str(row[obj_auth_idx] or "").strip().upper()
        field_name = str(row[field_name_idx] or "").strip().upper()

        role_name = row[role_idx]
        if not role_name:
            continue
        role_name = str(role_name).strip()

        if obj_auth == "S_TABU_DIS":
            for vidx in valor_indices:
                if vidx >= len(row):
                    continue
                if str(row[vidx] or "").strip() == "*":
                    tabu_dis_roles.add(role_name)
                    break
            continue

        if obj_auth != "S_TCODE" or field_name != "TCD":
            continue

        for vidx in valor_indices:
            if vidx >= len(row):
                continue
            raw_value = row[vidx]
            if not raw_value:
                continue
            # un mismo campo puede traer varios tcodes separados por coma/espacio
            for tcode in str(raw_value).replace(",", " ").split():
                tcode = tcode.strip().upper()
                if not tcode:
                    continue
                key = (role_name, tcode)
                if key in seen:
                    continue
                seen.add(key)
                db.session.add(SapRoleTcode(role_name=role_name, tcode=tcode, source="AGR_1251"))
                count += 1

    for role_name in tabu_dis_roles:
        db.session.add(SapRoleTableAuth(role_name=role_name))

    db.session.commit()
    invalidate_analysis_cache()
    return count


def import_agr_tcodes(file_stream):
    """AGR_TCODES.xlsx: alternativa/respaldo a AGR_1251 para tcode por rol.
    Columnas reales: Rol(0), Tp.report(1), Nombre ampliado(2), Excluido(3),
    Transaccion introducida directamente(4), Transaccion heredada de rol
    predecesor(5), ID(6). Solo las filas con Tp.report == 'TR' traen un
    tcode real en 'Nombre ampliado' (las 'OT' son apps Fiori/WebDynpro)."""
    headers, rows = read_excel_matrix(file_stream)

    role_idx = column_index(headers, ["ROL"])
    tp_report_idx = column_index(headers, ["TP.REPORT", "TP REPORT"])
    tcode_idx = column_index(headers, ["NOMBRE AMPLIADO"])
    excluido_idx = column_index(headers, ["EXCLUIDO"])

    if role_idx is None or tp_report_idx is None or tcode_idx is None:
        raise ValueError("AGR_TCODES.xlsx: estructura de columnas inesperada (Rol/Tp.report/Nombre ampliado).")

    SapRoleTcode.query.filter_by(source="AGR_TCODES").delete()

    count = 0
    seen = set()
    for row in rows:
        if role_idx >= len(row) or tp_report_idx >= len(row) or tcode_idx >= len(row):
            continue
        tp_report = str(row[tp_report_idx] or "").strip().upper()
        if tp_report != "TR":
            continue

        if excluido_idx is not None and excluido_idx < len(row):
            excluido_val = row[excluido_idx]
            if excluido_val and str(excluido_val).strip().upper() in ("X", "SI", "TRUE", "1"):
                continue

        role_name = row[role_idx]
        tcode = row[tcode_idx]
        if not role_name or not tcode:
            continue

        role_name = str(role_name).strip()
        tcode = str(tcode).strip().upper()
        key = (role_name, tcode)
        if key in seen:
            continue
        seen.add(key)
        db.session.add(SapRoleTcode(role_name=role_name, tcode=tcode, source="AGR_TCODES"))
        count += 1

    db.session.commit()
    invalidate_analysis_cache()
    return count


def import_agr_define(file_stream):
    """AGR_DEFINE.xlsx: descripcion de cada rol y su jerarquia rol simple ->
    rol compuesto. Columnas reales: Rol(0, rol "hijo", con datos), Rol(1,
    rol "padre"/compuesto -- vacio si el rol no pertenece a ningun
    compuesto, es decir es definitivo), Usuario(2)...Descripcion breve del
    rol(ultima columna).

    El rol padre se usa en el motor SOD (engine.build_maps) para expandir
    un rol compuesto asignado a un usuario a sus roles simples, que son
    los que realmente traen tcodes/apps Fiori en
    AGR_1251/AGR_TCODES/AGR_HIER (un rol compuesto en si mismo no suele
    tener autorizaciones propias)."""
    headers, rows = read_excel_matrix(file_stream)

    rol_indices = column_indices(headers, ["ROL"])
    role_idx = rol_indices[0] if rol_indices else None
    parent_idx = rol_indices[1] if len(rol_indices) > 1 else None
    desc_idx = column_index(headers, ["DESCRIPCION BREVE", "DESCRIPCION DEL ROL", "DESCRIPCION"])

    if role_idx is None or desc_idx is None:
        raise ValueError("AGR_DEFINE.xlsx: no se encontraron las columnas Rol/Descripcion.")

    SapRoleDescription.query.delete()

    count = 0
    seen = set()
    for row in rows:
        if role_idx >= len(row):
            continue
        role_name = row[role_idx]
        if not role_name:
            continue
        role_name = str(role_name).strip()
        if role_name in seen:
            continue
        seen.add(role_name)

        description = ""
        if desc_idx < len(row) and row[desc_idx]:
            description = str(row[desc_idx]).strip()

        parent_role = None
        if parent_idx is not None and parent_idx < len(row) and row[parent_idx]:
            parent_role = str(row[parent_idx]).strip() or None

        db.session.add(SapRoleDescription(
            role_name=role_name,
            description=description,
            parent_role=parent_role,
        ))
        count += 1

    db.session.commit()
    invalidate_analysis_cache()
    return count


def import_tstct(file_stream):
    """TSTCT.xlsx: descripcion corta de cada transaccion SAP (solo
    informativo, opcional). Columnas reales: Tcode y Texto/Descripcion
    (el nombre exacto de columna varia segun el export, se busca por
    coincidencia parcial)."""
    headers, rows = read_excel_matrix(file_stream)

    tcode_idx = column_index(headers, ["TCODE", "TRANSACCION", "CODIGO DE TRANSACCION"])
    desc_idx = column_index(headers, ["TEXTO", "DESCRIPCION", "TTEXT", "NOMBRE DE LA TRANSACCION"])

    if tcode_idx is None or desc_idx is None:
        raise ValueError("TSTCT.xlsx: no se encontraron las columnas Tcode/Descripcion.")

    SapTcodeDescription.query.delete()

    count = 0
    seen = set()
    for row in rows:
        if tcode_idx >= len(row):
            continue
        tcode = row[tcode_idx]
        if not tcode:
            continue
        tcode = str(tcode).strip().upper()
        if tcode in seen:
            continue
        seen.add(tcode)

        description = ""
        if desc_idx < len(row) and row[desc_idx]:
            description = str(row[desc_idx]).strip()

        db.session.add(SapTcodeDescription(tcode=tcode, description=description))
        count += 1

    db.session.commit()
    invalidate_analysis_cache()
    return count


def import_agr_hier(file_stream):
    """AGR_HIER.xlsx: jerarquia de menu de cada rol. Solo interesan los
    nodos OT (objeto Fiori) de tipo CAT_PROVIDER o GROUP_PROVIDER -- los
    catalogos y grupos de negocio Fiori asignados al rol; el resto de
    AGR_HIER (tcodes clasicos, carpetas de menu) ya lo cubren
    AGR_1251/AGR_TCODES. Columnas reales: Rol(0), Contador para ID
    menu(1), Tp.report(5), Nombre ampliado(6).

    Al terminar, recalcula los tcodes Fiori por rol (recompute_fiori_tcodes)
    porque este archivo es uno de los 3 que intervienen en esa resolucion
    (junto con AGR_BUFFI y PB_C_CHIPM) y puede importarse en cualquier
    orden."""
    headers, rows = read_excel_matrix(file_stream)

    role_idx = column_index(headers, ["ROL"])
    contador_idx = column_index(headers, ["CONTADOR PARA ID MENU"])
    tp_idx = column_index(headers, ["TP.REPORT", "TP REPORT"])
    amp_idx = column_index(headers, ["NOMBRE AMPLIADO"])

    if None in (role_idx, contador_idx, tp_idx, amp_idx):
        raise ValueError("AGR_HIER.xlsx: estructura de columnas inesperada (Rol/Contador/Tp.report/Nombre ampliado).")

    SapRoleHierNode.query.delete()

    count = 0
    for row in rows:
        if max(role_idx, contador_idx, tp_idx, amp_idx) >= len(row):
            continue
        if str(row[tp_idx] or "").strip().upper() != "OT":
            continue
        kind = str(row[amp_idx] or "").strip().upper()
        if kind not in ("CAT_PROVIDER", "GROUP_PROVIDER"):
            continue

        role_name = row[role_idx]
        contador = row[contador_idx]
        if not role_name or contador is None or str(contador).strip() == "":
            continue

        db.session.add(SapRoleHierNode(
            role_name=str(role_name).strip(),
            contador=str(contador).strip(),
            kind=kind,
        ))
        count += 1

    db.session.commit()
    recompute_fiori_tcodes()
    return count


def import_agr_buffi(file_stream):
    """AGR_BUFFI.xlsx: URL de cada nodo de menu de un rol. Unida con
    SapRoleHierNode (mismo Rol + Contador) permite extraer el ID tecnico
    del catalogo/grupo Fiori. Columnas reales: Rol(0), Contador para ID
    menu(1), Tipo de URL(2), Enlace de Internet(3).

    Al terminar, recalcula los tcodes Fiori por rol (ver import_agr_hier)."""
    headers, rows = read_excel_matrix(file_stream)

    role_idx = column_index(headers, ["ROL"])
    contador_idx = column_index(headers, ["CONTADOR PARA ID MENU"])
    url_idx = column_index(headers, ["ENLACE DE INTERNET", "ENLACE INTERNET"])

    if None in (role_idx, contador_idx, url_idx):
        raise ValueError("AGR_BUFFI.xlsx: estructura de columnas inesperada (Rol/Contador/Enlace de Internet).")

    SapBuffiUrl.query.delete()

    count = 0
    for row in rows:
        if max(role_idx, contador_idx, url_idx) >= len(row):
            continue
        role_name = row[role_idx]
        contador = row[contador_idx]
        url = row[url_idx]
        if not role_name or contador is None or str(contador).strip() == "" or not url:
            continue

        db.session.add(SapBuffiUrl(
            role_name=str(role_name).strip(),
            contador=str(contador).strip(),
            url=str(url).strip(),
        ))
        count += 1

    db.session.commit()
    recompute_fiori_tcodes()
    return count


_TCODE_FROM_SEMANTIC_RE = re.compile(r"^[A-Z0-9_./]{2,25}$")


def _tcode_from_semantic_object(sem_obj):
    """Heuristica validada contra el PB_C_CHIPM real de Grupo Simpa: cuando
    un chip Fiori no trae transaction.code directo, en la enorme mayoria de
    los casos restantes el 'objeto semantico' es el propio tcode clasico
    disfrazado con un prefijo 'Z_' o 'Z' para exponerlo en el Launchpad
    (ej. 'Z_SM35' o 'ZMD01N' -> SM35/MD01N). Un objeto semantico Fiori
    genuino (app OData real, sin tcode) usa palabras en ingles con
    mayuscula/minuscula mixta (ej. 'ProfitCenter', 'BusinessProcess'): esa
    es la senal que distingue ambos casos, por eso se exige que el texto
    original venga todo en mayusculas."""
    if not sem_obj:
        return None
    raw = sem_obj.strip()
    if not raw or raw != raw.upper():
        return None

    s = raw.upper()
    if s.startswith("Z_"):
        candidate = s[2:]
        if _TCODE_FROM_SEMANTIC_RE.match(candidate):
            s = candidate
    elif s.startswith("Z") and len(s) > 1:
        candidate = s[1:]
        if _TCODE_FROM_SEMANTIC_RE.match(candidate):
            s = candidate

    if _TCODE_FROM_SEMANTIC_RE.match(s) and not s.isdigit():
        return s
    return None


def _extract_tcode_from_config(config_raw):
    """Extrae el tcode y el titulo visible del chip desde la columna
    CONFIGURATION de PB_C_CHIPM (JSON anidado: el JSON externo trae una
    clave 'tileConfiguration' cuyo valor es, a su vez, un JSON serializado
    como texto). Devuelve (tcode, titulo, raw_sem_obj):
    - tcode: directo (transaction.code) o derivado del objeto semantico
      (ver _tcode_from_semantic_object); None para apps Fiori OData
      genuinas, sin tcode equivalente.
    - titulo: el campo 'display_title_text' del chip (cadena vacia si no
      esta presente). Informativo, fallback en picker cuando TSTCT no cubre.
    - raw_sem_obj: el semantic_object crudo del JSON, para la resolucion
      Priority 3 via SapFioriAppReg en recompute_fiori_tcodes."""
    if not config_raw:
        return None, "", ""
    try:
        outer = json.loads(config_raw)
    except (ValueError, TypeError):
        return None, "", ""
    inner_raw = outer.get("tileConfiguration") if isinstance(outer, dict) else None
    if not inner_raw:
        return None, "", ""
    try:
        inner = json.loads(inner_raw)
    except (ValueError, TypeError):
        return None, "", ""
    if not isinstance(inner, dict):
        return None, "", ""

    titulo = (inner.get("display_title_text") or "").strip()

    tcode = ((inner.get("transaction") or {}).get("code") or "").strip()
    if tcode:
        return tcode.upper(), titulo, ""

    sem_obj = (inner.get("navigation_semantic_object") or inner.get("semantic_object") or "").strip()
    return _tcode_from_semantic_object(sem_obj), titulo, sem_obj


def _read_pb_c_chipm_rows(file_stream):
    """PB_C_CHIPM se sube normalmente como .xlsx, igual que el resto de los
    exports. Si el archivo no se puede abrir como Excel (por ejemplo, un
    .xlsx que se corrompio en la descarga, o directamente un .csv exportado
    como alternativa), se reintenta como CSV de SAP (codificacion
    ISO-8859-1, separador ';'), que es el formato que efectivamente se
    valido para este archivo."""
    try:
        return read_excel_matrix(file_stream)
    except Exception:
        pass

    file_stream.seek(0)
    raw_bytes = file_stream.read()
    text = raw_bytes.decode("iso-8859-1")
    reader = csv.reader(io.StringIO(text), delimiter=";")
    all_rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not all_rows:
        raise ValueError("PB_C_CHIPM: no se pudo leer el archivo ni como Excel ni como CSV.")

    headers = [_strip_accents(str(c or "").strip().upper()) for c in all_rows[0]]
    return headers, all_rows[1:]


def import_pb_c_chipm(file_stream):
    """PB_C_CHIPM.xlsx: chips (tiles) de cada catálogo/grupo Fiori
    personalizado. Guarda los datos crudos en SapChipCatalog (preservando
    el component_id/CHIPID para poder resolverlo más tarde con
    SapFioriAppReg/SUI_TM_MM_APP) y luego llama a recompute_fiori_tcodes()
    para calcular los tcodes efectivos con ambas fuentes.

    Al terminar, recalcula los tcodes Fiori por rol (ver import_agr_hier)."""
    headers, rows = _read_pb_c_chipm_rows(file_stream)

    # Columna 1: ID de catálogo (X-SAP-UI2-CATALOGPAGE:ZSD_CAT_FACTURAS_2)
    page_indices = column_indices(headers, ["ID DE CATALOGO"])
    page_idx = page_indices[0] if page_indices else None
    # Columna 2: ID de instancia de chip (clave única dentro del catálogo)
    inst_idx = page_indices[1] if len(page_indices) > 1 else None
    config_idx = column_index(headers, ["CONFIGURATION"])
    # Hay 3 columnas "ID de CHIP" en PB_C_CHIPM:
    #   [0]: ID completo de la instancia (X-SAP-UI2-PAGE:...CAT...:INST_ID)
    #   [1]: tipo de chip (/UI2/ACTION, /UI2/STATIC_APPLAUNCHER...)
    #   [2]: referencia ADCHIP (X-SAP-UI2-ADCHIP:...:ADID_TM)
    chip_indices = column_indices(headers, ["ID DE CHIP"])
    adchip_col = chip_indices[2] if len(chip_indices) > 2 else None

    if page_idx is None or config_idx is None:
        raise ValueError("PB_C_CHIPM: estructura de columnas inesperada (ID de catalogo/CONFIGURATION).")

    # Patrón para extraer el ADID de una referencia X-SAP-UI2-ADCHIP
    # Ej: X-SAP-UI2-ADCHIP:X-SAP-UI2-ADCAT:SAP_TC_CEC_SD_BE_APPS:S4SD:FA163EDF..._TM
    _ADCHIP_RE = re.compile(
        r"X-SAP-UI2-ADCHIP:.*:([A-F0-9]{32})(?:_TM|_AL|_TML)?",
        re.IGNORECASE,
    )
    # Patrón para referencias de página a otro catálogo SAP estándar:
    # X-SAP-UI2-PAGE:X-SAP-UI2-CATALOGPAGE:SAP_TC_CEC_SD_COMMON:00O2TO8X710...
    _PAGE_REF_RE = re.compile(
        r"X-SAP-UI2-PAGE:X-SAP-UI2-CATALOGPAGE:([^:]+):",
        re.IGNORECASE,
    )

    SapChipCatalog.query.delete()

    seen = set()  # (cat_or_grp_id, inst_id) — único por instancia de chip en el catálogo
    count = 0
    for row in rows:
        if max(page_idx, config_idx) >= len(row):
            continue
        page_raw = str(row[page_idx] or "").strip()
        if not page_raw:
            continue

        if page_raw.upper().startswith("X-SAP-UI2-CATALOGPAGE:"):
            cat_or_grp_id = page_raw.split(":", 1)[1].strip()
        else:
            cat_or_grp_id = page_raw
        if not cat_or_grp_id:
            continue

        # ID de instancia: col 2 (único por chip dentro del catálogo)
        inst_id = ""
        if inst_idx is not None and inst_idx < len(row):
            inst_id = str(row[inst_idx] or "").strip()

        # Dedup por (catálogo, instancia); si no hay inst_id, caer en config
        config_raw = str(row[config_idx] or "").strip() if config_idx < len(row) else ""
        key = (cat_or_grp_id, inst_id) if inst_id else (cat_or_grp_id, config_raw[:80])
        if key in seen:
            continue
        seen.add(key)

        # component_id: ADID extraído de col 6 (para chips SAP estándar)
        # page_ref_catalog: catálogo fuente cuando col 6 es X-SAP-UI2-PAGE:...:SRC_CATALOG:SLOT
        component_id = ""
        page_ref_catalog = ""
        if adchip_col is not None and adchip_col < len(row):
            adchip_ref = str(row[adchip_col] or "").strip()
            m = _ADCHIP_RE.match(adchip_ref)
            if m:
                component_id = m.group(1).upper()
            else:
                pm = _PAGE_REF_RE.match(adchip_ref)
                if pm:
                    page_ref_catalog = pm.group(1).strip()

        db.session.add(SapChipCatalog(
            catalog_or_group_id=cat_or_grp_id,
            component_id=component_id,
            config_raw=config_raw,
            page_ref_catalog=page_ref_catalog,
        ))
        count += 1

    db.session.commit()
    recompute_fiori_tcodes()
    return count


def _parse_fiori_id_from_url(url):
    """Extrae el ID tecnico del catalogo/grupo Fiori embebido en la URL de
    AGR_BUFFI (formato 'X-SAP-UI2-<TIPO>:<ID>?<resto>'): se separa por ':'
    y se toma la segunda parte, cortando en el primer '?' si lo hay."""
    if not url or ":" not in url:
        return None
    part = url.split(":", 1)[1]
    return part.split("?", 1)[0].strip() or None


def recompute_fiori_tcodes():
    """Recalcula los tcodes que cada rol obtiene via apps Fiori y los
    guarda como SapRoleTcode(source='FIORI') y SapFioriIdTcode.

    Fuentes de resolución (en orden de prioridad para cada chip):
      1. transaction.code directo en el JSON config de PB_C_CHIPM.
      2. Lookup del component_id (CHIPID) en SapFioriAppReg (SUI_TM_MM_APP)
         → campo estructurado, más confiable que el parsing JSON.
      3. Heurística de objeto semántico Z_<TCODE>/Z<TCODE> (fallback).

    Se llama al terminar de importar cualquiera de los archivos
    involucrados (AGR_HIER, AGR_BUFFI, PB_C_CHIPM, SUI_TM_MM_APP) para
    que el orden de importación no afecte el resultado."""

    # 1. Cargar registro de apps Fiori de SUI_TM_MM_APP (si fue importado)
    #    Claves: app_id corto (F0797), ADID (FA163EDF...) y semantic_object
    app_reg = {}     # app_id o adid → (tcode, titulo)
    sem_reg = {}     # semantic_object.lower() → (tcode, titulo)
    for row in SapFioriAppReg.query.all():
        val = (row.tcode, row.titulo)
        for key in filter(None, [row.app_id, row.adid]):
            if key not in app_reg or (not app_reg[key][1] and row.titulo):
                app_reg[key] = val
        if row.semantic_object:
            skey = row.semantic_object.lower()
            if skey not in sem_reg or (not sem_reg[skey][1] and row.titulo):
                sem_reg[skey] = val

    # 2. Resolver catalog_or_group_id → set de tcodes desde SapChipCatalog
    id_to_tcodes = defaultdict(set)   # catalog_id → {tcode}
    id_to_titulos = {}                 # (catalog_id, tcode) → titulo

    # P0: CATID directo de SUI_TM_MM_APP (catálogo nativo SAP de cada app).
    # Ej: (SAP_TC_CEC_SD_COMMON, F0797) — se inserta antes de procesar chips
    # para que P4 (herencia PAGE refs) pueda propagarlo a catálogos custom.
    for row in SapFioriAppReg.query.filter(
        SapFioriAppReg.catid != "", SapFioriAppReg.catid != None  # noqa: E711
    ).all():
        if row.catid and row.tcode:
            id_to_tcodes[row.catid].add(row.tcode)
            if row.titulo and (row.catid, row.tcode) not in id_to_titulos:
                id_to_titulos[(row.catid, row.tcode)] = row.titulo

    for chip in SapChipCatalog.query.all():
        cat_id = chip.catalog_or_group_id

        # Prioridad 1: transaction.code directo del JSON config
        tcode, titulo, raw_sem_obj = _extract_tcode_from_config(chip.config_raw)

        # Prioridad 2: component_id exacto en SapFioriAppReg (SUI_TM_MM_APP app_id)
        if not tcode and chip.component_id and chip.component_id in app_reg:
            tcode, titulo = app_reg[chip.component_id]

        # Prioridad 3: objeto semantico del chip JSON vs SapFioriAppReg
        # Cubre apps OData reales (ej. F0805A) donde app_id != component_id
        if not tcode and raw_sem_obj:
            match = sem_reg.get(raw_sem_obj.lower())
            if match:
                tcode, titulo = match

        if tcode:
            id_to_tcodes[cat_id].add(tcode)
            if titulo and (cat_id, tcode) not in id_to_titulos:
                id_to_titulos[(cat_id, tcode)] = titulo

    # P4: Herencia de catálogo via referencias PAGE en PB_C_CHIPM.
    # Cuando un catálogo custom (ZSD_CAT_FACTURAS_2) incluye chips de un
    # catálogo SAP estándar (SAP_TC_CEC_SD_COMMON) via X-SAP-UI2-PAGE:...,
    # propagamos sus tcodes al catálogo custom.
    page_pairs = (
        db.session.query(
            SapChipCatalog.catalog_or_group_id,
            SapChipCatalog.page_ref_catalog,
        )
        .filter(
            SapChipCatalog.page_ref_catalog != "",
            SapChipCatalog.page_ref_catalog != None,  # noqa: E711
        )
        .distinct()
        .all()
    )
    for custom_cat, src_cat in page_pairs:
        for tcode in id_to_tcodes.get(src_cat, set()):
            id_to_tcodes[custom_cat].add(tcode)
            if (src_cat, tcode) in id_to_titulos and (custom_cat, tcode) not in id_to_titulos:
                id_to_titulos[(custom_cat, tcode)] = id_to_titulos[(src_cat, tcode)]

    # 3. Reconstruir SapFioriIdTcode desde los tcodes resueltos
    SapFioriIdTcode.query.delete()
    for cat_id, tcodes in id_to_tcodes.items():
        for tcode in tcodes:
            db.session.add(SapFioriIdTcode(
                catalog_or_group_id=cat_id,
                tcode=tcode,
                titulo=id_to_titulos.get((cat_id, tcode), ""),
            ))

    # 4. Resolver rol → tcodes Fiori via AGR_HIER + AGR_BUFFI
    SapRoleTcode.query.filter_by(source="FIORI").delete()

    if not id_to_tcodes:
        db.session.commit()
        invalidate_analysis_cache()
        return 0

    url_by_role_contador = {
        (row.role_name, row.contador): row.url for row in SapBuffiUrl.query.all()
    }

    role_tcodes = defaultdict(set)
    for node in SapRoleHierNode.query.all():
        url = url_by_role_contador.get((node.role_name, node.contador))
        fiori_id = _parse_fiori_id_from_url(url)
        if not fiori_id:
            continue
        tcodes = id_to_tcodes.get(fiori_id)
        if tcodes:
            role_tcodes[node.role_name] |= tcodes

    count = 0
    for role_name, tcodes in role_tcodes.items():
        for tcode in tcodes:
            db.session.add(SapRoleTcode(role_name=role_name, tcode=tcode, source="FIORI"))
            count += 1

    db.session.commit()

    # 5. Propagar tcodes de roles hijos a roles padres
    _propagate_composite_tcodes()

    invalidate_analysis_cache()
    return count


def _propagate_composite_tcodes():
    """Propaga tcodes (AGR_1251, AGR_TCODES, FIORI) de roles hijos a sus
    roles padres (compuestos).

    En SAP los roles compuestos no tienen entradas directas en AGR_1251 —
    los tcodes viven en los roles simples (hijos). Esta función crea entradas
    source='COMPOSITE' en SapRoleTcode para que el padre aparezca en:
      - búsquedas de RolesDB por transacción
      - matriz SOD (build_maps ya expande vía parent_role, pero tener los
        registros explícitos evita dependencias del orden de carga)

    Se ejecuta al final de recompute_fiori_tcodes() y también puede
    llamarse manualmente desde la ruta de importación.
    """
    # Borrar propagaciones previas para recalcular desde cero
    SapRoleTcode.query.filter_by(source="COMPOSITE").delete()
    db.session.flush()

    # Construir mapa padre → [hijos]
    children_by_parent: dict[str, list[str]] = {}
    for row in SapRoleDescription.query.filter(
        SapRoleDescription.parent_role.isnot(None)
    ).all():
        children_by_parent.setdefault(row.parent_role, []).append(row.role_name)

    if not children_by_parent:
        db.session.commit()
        return 0

    added = 0
    for parent, children in children_by_parent.items():
        # Todos los tcodes de los hijos (cualquier source)
        child_tcodes = {
            r.tcode
            for r in db.session.query(SapRoleTcode.tcode)
            .filter(SapRoleTcode.role_name.in_(children))
            .distinct()
            .all()
        }
        # Tcodes que el padre ya tiene directamente (no duplicar)
        existing = {
            r.tcode
            for r in db.session.query(SapRoleTcode.tcode)
            .filter_by(role_name=parent)
            .all()
        }
        for tcode in child_tcodes - existing:
            db.session.add(SapRoleTcode(role_name=parent, tcode=tcode, source="COMPOSITE"))
            added += 1

    db.session.commit()
    return added


def import_sui_tm_mm_app(file_stream):
    """SUI_TM_MM_APP.xlsx: tabla SAP con el mapeo estructurado de app Fiori
    -> transaccion. Exportar desde SE16N -> tabla SUI_TM_MM_APP.

    Columnas principales (SE16N en español):
      - ID de aplicación (APPID): GUID largo del app
      - ID de catálogo   (CATID): ID técnico del catálogo Fiori
      - Código de transacción (TCODE): el tcode o app Fiori ID
      - Objeto semántico (SEMOBJ)
      - ADID: App Descriptor ID (FA163EDF...) — opcional

    Estrategia de resolución:
      1. Si el Excel trae CATID: rellena SapFioriIdTcode directamente
         (catálogo → tcode) sin necesidad de ADID ni PB_C_CHIPM.
      2. Siempre rellena SapFioriAppReg (app_id / adid → tcode).
      3. Llama a recompute_fiori_tcodes() para resolver via ADID los chips
         de PB_C_CHIPM que no pudieron resolverse por config JSON.
    """
    headers, rows = read_excel_matrix(file_stream)

    app_id_idx = column_index(headers, [
        "ID DE APLICACION", "APPID", "APP ID", "APP_ID",
        "APPLICATION ID", "FIORI APP ID", "ID APLICACION",
    ])
    tcode_idx = column_index(headers, [
        "CODIGO TRANSACCION", "CODIGO DE TRANSACCION",
        "TCODE", "TRANSACTION", "TRANSACTION CODE", "COD.TRANSACCION",
    ])
    # CATID = ID de catálogo técnico (ej. ZSD_CAT_FACTURAS_2) — mapeo directo sin ADID
    # "ID DE CATALOGO" es substring de "ID DE CATALOGO TECNICO", así que matchea ambos
    catid_idx = column_index(headers, [
        "ID DE CATALOGO TECNICO", "ID DE CATALOGO", "CATID", "CAT ID",
        "CATALOG ID", "ID CATALOGO", "CATALOGUE ID", "CAT_ID",
    ])
    titulo_idx = column_index(headers, [
        "TITULO", "TITLE", "DESCRIPCION", "DESCRIPTION",
        "TEXTO", "TEXT", "APP TITLE", "NOMBRE",
    ])
    semobj_idx = column_index(headers, [
        "OBJETO SEMANTICO", "SEMANTIC OBJECT", "SEMOBJ",
        "SEMANTIC_OBJECT", "OBJETO SEM",
    ])
    # ADID = App Descriptor ID — en SUI_TM_MM_APP la col "ID de aplicación" (APP_ID)
    # es el ADID. Se guarda en ambos campos: app_id (para búsqueda) y adid (para recompute).
    adid_idx = column_index(headers, [
        "ID DE APLICACION", "ADID", "APP DESCRIPTOR", "DESCRIPTOR ID",
        "ID DESCRIPTOR", "ID DE DESCRIPTOR", "AD ID", "APP_ID",
    ])

    if app_id_idx is None or tcode_idx is None:
        raise ValueError(
            "SUI_TM_MM_APP: no se encontraron las columnas de App ID y/o "
            "Transaccion. "
            "Columnas detectadas: " + ", ".join(headers[:15])
        )

    SapFioriAppReg.query.delete()

    seen_reg = set()
    count = 0

    for row in rows:
        if max(app_id_idx, tcode_idx) >= len(row):
            continue
        app_id = str(row[app_id_idx] or "").strip()
        tcode  = str(row[tcode_idx]  or "").strip().upper()
        if not app_id or not tcode:
            continue

        titulo = ""
        if titulo_idx is not None and titulo_idx < len(row):
            titulo = str(row[titulo_idx] or "").strip()

        semantic_object = ""
        if semobj_idx is not None and semobj_idx < len(row):
            semantic_object = str(row[semobj_idx] or "").strip()

        adid = ""
        if adid_idx is not None and adid_idx < len(row):
            adid = str(row[adid_idx] or "").strip()

        catid = ""
        if catid_idx is not None and catid_idx < len(row):
            catid = str(row[catid_idx] or "").strip()

        # ── SapFioriAppReg ──────────────────────────────────────────────
        # catid se guarda aquí y recompute_fiori_tcodes() lo procesa en P0
        # (no se inserta directo en SapFioriIdTcode porque recompute lo borra)
        key_reg = (app_id, tcode)
        if key_reg not in seen_reg:
            seen_reg.add(key_reg)
            db.session.add(SapFioriAppReg(
                app_id=app_id,
                adid=adid,
                tcode=tcode,
                titulo=titulo,
                semantic_object=semantic_object,
                catid=catid,
            ))
            count += 1

    db.session.commit()

    # Además, resolver via ADID los chips de PB_C_CHIPM que no tienen config JSON
    recompute_fiori_tcodes()
    return count



def import_usr02(file_stream):
    """USR02.xlsx: estado de cuenta de cada usuario SAP (tabla USR02
    estandar). Columnas: Usuarios, Tipo usuario, Valido de, Fin validez,
    Intentos fallidos, Status de bloqueo usuario, Fecha ultimo acceso.

    bloqueado se deriva de Status de bloqueo: 0 = no bloqueado, cualquier
    otro valor = bloqueado (32=intentos fallidos, 64=admin, 128=CUA)."""
    headers, rows = read_excel_matrix(file_stream)

    user_idx = column_index(headers, ["USUARIOS"])
    tipo_idx = column_index(headers, ["TIPO USUARIO"])
    valido_desde_idx = column_index(headers, ["VALIDO DE"])
    valido_hasta_idx = column_index(headers, ["FIN VALIDEZ"])
    intentos_idx = column_index(headers, [
        "INTENTOS ACCESO SIST.FALLIDOS",
        "INTENTOS ACCESO SISTEMA FALLIDOS",
        "CTD.INTENTOS ACCESO SIST.FALLIDOS",
    ])
    bloqueo_idx = column_index(headers, [
        "STATUS DE BLOQUEO USUARIO",
        "STATUS DE BLOQUEO",
        "UFLAG",
    ])
    ultimo_login_idx = column_index(headers, [
        "FECHA ULTIMO ACCESO AL SISTEMA",
        "ULTIMO ACCESO AL SISTEMA",
        "FECHA DE ULTIMO LOGON",
    ])

    if user_idx is None or bloqueo_idx is None:
        raise ValueError(
            "USR02.xlsx: estructura de columnas inesperada. "
 
            "Se requieren Usuarios y Status de bloqueo usuario. "
            "Columnas detectadas: " + ", ".join(headers[:10])
        )

    SapUserStatus.query.delete()

    count = 0
    seen = set()
    for row in rows:
        if user_idx >= len(row) or bloqueo_idx >= len(row):
            continue
        username = str(row[user_idx] or "").strip()
        if not username or username in seen:
            continue
        seen.add(username)

        lock_raw = str(row[bloqueo_idx] or "0").strip()
        bloqueado = lock_raw != "0"

        tipo_usuario = ""
        if tipo_idx is not None and tipo_idx < len(row):
            tipo_usuario = str(row[tipo_idx] or "").strip()

        intentos = 0
        if intentos_idx is not None and intentos_idx < len(row):
            try:
                intentos = int(row[intentos_idx] or 0)
            except (ValueError, TypeError):
                intentos = 0

        valido_desde = None
        if valido_desde_idx is not None and valido_desde_idx < len(row):
            valido_desde = parse_excel_date(row[valido_desde_idx])

        valido_hasta = None
        if valido_hasta_idx is not None and valido_hasta_idx < len(row):
            valido_hasta = parse_excel_date(row[valido_hasta_idx])

        ultimo_login = None
        if ultimo_login_idx is not None and ultimo_login_idx < len(row):
            ultimo_login = parse_excel_date(row[ultimo_login_idx])

        db.session.add(SapUserStatus(
            username=username,
            tipo_usuario=tipo_usuario,
            bloqueado=bloqueado,
            lock_status_raw=lock_raw,
            intentos_fallidos=intentos,
            valido_desde=valido_desde,
            valido_hasta=valido_hasta,
            ultimo_login=ultimo_login,
        ))
        count += 1

    db.session.commit()
    return count


def import_agr_1252(file_stream):
    """AGR_1252.xlsx: valores de nivel organizacional (variables USVAR,
    ej. $WERKS, $BUKRS) asignados a cada rol. Columnas reales: Rol(0),
    ID(1), Nivel org.(2), Valor de la autorizacion BAJO(3), Valor de la
    autorizacion ALTO(4, encabezado duplicado -- igual patron que
    AGR_1251.xlsx). Una fila por cada valor autorizado (o por cada
    extremo de un rango); el valor puede venir vacio."""
    headers, rows = read_excel_matrix(file_stream)

    role_idx = column_index(headers, ["ROL"])
    nivel_idx = column_index(headers, ["NIVEL ORG"])
    valor_indices = column_indices(headers, ["VALOR DE LA AUTORIZACION", "VALOR AUTORIZACION"])

    if role_idx is None or nivel_idx is None or not valor_indices:
        raise ValueError(
            "AGR_1252.xlsx: estructura de columnas inesperada (Rol/Nivel org./Valor de la autorizacion). "
            "Columnas detectadas: " + ", ".join(headers[:10])
        )

    # Bajo = primer indice encontrado, Alto = segundo (si el export lo trae)
    bajo_idx = valor_indices[0]
    alto_idx = valor_indices[1] if len(valor_indices) > 1 else None

    SapRoleOrgLevel.query.delete()

    count = 0
    for row in rows:
        if role_idx >= len(row) or nivel_idx >= len(row):
            continue
        role_name = row[role_idx]
        nivel = row[nivel_idx]
        if not role_name or not nivel:
            continue

        valor_bajo = ""
        if bajo_idx is not None and bajo_idx < len(row) and row[bajo_idx] is not None:
            valor_bajo = str(row[bajo_idx]).strip()

        valor_alto = ""
        if alto_idx is not None and alto_idx < len(row) and row[alto_idx] is not None:
            valor_alto = str(row[alto_idx]).strip()

        db.session.add(SapRoleOrgLevel(
            role_name=str(role_name).strip(),
            nivel_codigo=str(nivel).strip(),
            valor_bajo=valor_bajo,
            valor_alto=valor_alto,
        ))
        count += 1

    db.session.commit()
    invalidate_analysis_cache()
    return count


def import_usvar(file_stream):
    """USVAR.xlsx: catalogo de variables de nivel organizacional (tabla
    USVAR estandar). Columnas: Variable(0), Longitud del string de
    valores en byte(1), Texto(2). Solo se usa como texto descriptivo
    junto al codigo tecnico en el detalle de rol (ver SapRoleOrgLevel)."""
    headers, rows = read_excel_matrix(file_stream)

    var_idx = column_index(headers, ["VARIABLE"])
    texto_idx = column_index(headers, ["TEXTO"])

    if var_idx is None:
        raise ValueError(
            "USVAR.xlsx: estructura de columnas inesperada (Variable). "
            "Columnas detectadas: " + ", ".join(headers[:10])
        )

    SapOrgLevelDesc.query.delete()

    count = 0
    seen = set()
    for row in rows:
        if var_idx >= len(row):
            continue
        codigo = row[var_idx]
        if not codigo:
            continue
        codigo = str(codigo).strip()
        if codigo in seen:
            continue
        seen.add(codigo)

        descripcion = ""
        if texto_idx is not None and texto_idx < len(row) and row[texto_idx] is not None:
            descripcion = str(row[texto_idx]).strip()

        db.session.add(SapOrgLevelDesc(codigo=codigo, descripcion=descripcion))
        count += 1

    db.session.commit()
    return count
