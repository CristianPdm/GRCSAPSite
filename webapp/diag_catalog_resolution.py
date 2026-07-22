"""Diagnóstico: ¿pueden resolverse los chips de ZSD_CAT_FACTURAS_2 a tcodes?
Muestra qué comp_ids tienen los chips del catálogo custom y si matchean
con algún app_id en SapFioriAppReg (importado de SUI_TM_MM_APP).
Uso: cd webapp && python diag_catalog_resolution.py [CATALOG_ID]
"""
import sys

catalog_id = sys.argv[1] if len(sys.argv) > 1 else "ZSD_CAT_FACTURAS_2"

from app import create_app
from app.extensions import db
from app.sod.models import SapChipCatalog, SapFioriAppReg, SapFioriIdTcode

app = create_app()
with app.app_context():

    sep = lambda t: print(f"\n{'='*65}\n{t}\n{'='*65}")

    # ── 1. Chips del catálogo custom ──────────────────────────────────────────
    sep(f"1. Chips en {catalog_id} (SapChipCatalog)")
    chips = SapChipCatalog.query.filter_by(catalog_or_group_id=catalog_id).all()
    print(f"   Total chips: {len(chips)}")

    comp_ids = []
    for c in chips:
        if c.component_id:
            comp_ids.append(c.component_id)

    print(f"   Con comp_id no vacío: {len(comp_ids)}")
    for cid in comp_ids[:10]:
        print(f"     {cid!r}")
    if len(comp_ids) > 10:
        print(f"     ... y {len(comp_ids)-10} más")

    # ── 2. ¿Cuántos comp_ids matchean SapFioriAppReg.app_id? ─────────────────
    sep("2. Match comp_id vs SapFioriAppReg.app_id")
    matched = []
    unmatched = []
    for cid in comp_ids:
        rows = SapFioriAppReg.query.filter_by(app_id=cid).all()
        if rows:
            for r in rows:
                matched.append((cid, r.tcode, r.titulo))
        else:
            unmatched.append(cid)

    print(f"   Matches encontrados: {len(matched)}")
    for cid, tcode, titulo in matched[:20]:
        print(f"     comp_id={cid!r}  →  tcode={tcode!r}  titulo={titulo[:40]!r}")

    print(f"\n   Sin match en SapFioriAppReg: {len(unmatched)}")
    for cid in unmatched[:10]:
        print(f"     {cid!r}")

    # ── 3. ¿Cuántos comp_ids matchean SapFioriAppReg.adid? ───────────────────
    sep("3. Match comp_id vs SapFioriAppReg.adid")
    matched_adid = []
    for cid in comp_ids:
        rows = SapFioriAppReg.query.filter(SapFioriAppReg.adid == cid).all()
        if rows:
            for r in rows:
                matched_adid.append((cid, r.tcode, r.titulo))
    print(f"   Matches por adid: {len(matched_adid)}")
    for cid, tcode, titulo in matched_adid[:20]:
        print(f"     comp_id={cid!r}  →  tcode={tcode!r}  titulo={titulo[:40]!r}")

    # ── 4. Estado actual de SapFioriIdTcode para este catálogo ───────────────
    sep(f"4. SapFioriIdTcode ya resueltos para {catalog_id}")
    resolved = SapFioriIdTcode.query.filter_by(catalog_or_group_id=catalog_id).all()
    print(f"   Tcodes ya en SapFioriIdTcode: {len(resolved)}")
    for r in resolved[:20]:
        print(f"     tcode={r.tcode!r}  titulo={r.titulo!r}")

    # ── 5. ¿Qué valores tiene F0797 en SapFioriAppReg? ───────────────────────
    sep("5. SapFioriAppReg para F0797")
    rows = SapFioriAppReg.query.filter_by(tcode="F0797").all()
    print(f"   Filas: {len(rows)}")
    for r in rows:
        print(f"   app_id={r.app_id!r}")
        print(f"   adid  ={r.adid!r}")
        print(f"   titulo={r.titulo!r}")
        print(f"   sem_obj={r.semantic_object!r}")
        print()

    # ── 6. Resumen ────────────────────────────────────────────────────────────
    sep("RESUMEN")
    total = len(comp_ids)
    via_app_id = len(set(cid for cid, _, _ in matched))
    via_adid   = len(set(cid for cid, _, _ in matched_adid))
    ya_resueltos = len(resolved)
    print(f"  Chips en {catalog_id}: {len(chips)} ({total} con comp_id)")
    print(f"  Matchean por app_id : {via_app_id}/{total}")
    print(f"  Matchean por adid   : {via_adid}/{total}")
    print(f"  Ya en SapFioriIdTcode: {ya_resueltos}")
    if via_app_id == 0 and via_adid == 0:
        print("\n  PROBLEMA: ningún comp_id del catálogo matchea SapFioriAppReg.")
        print("  Revisá qué columna de SUI_TM_MM_APP contiene los valores de")
        print("  los comp_ids de PB_C_CHIPM para este catálogo.")
