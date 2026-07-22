"""Script de diagnóstico para resolver por qué un tcode Fiori no aparece.
Uso: cd webapp && python diag_fiori.py F0797
"""
import sys
import json

tcode_buscado = (sys.argv[1] if len(sys.argv) > 1 else "F0797").upper()

from app import create_app
from app.extensions import db
from app.sod.models import (
    SapChipCatalog, SapFioriIdTcode, SapFioriAppReg,
    SapRoleHierNode, SapBuffiUrl, SapRoleTcode,
)

app = create_app()
with app.app_context():

    sep = lambda t: print(f"\n{'='*60}\n{t}\n{'='*60}")

    # 1. ¿Está resuelto en SapFioriIdTcode?
    sep(f"1. SapFioriIdTcode para {tcode_buscado}")
    rows = SapFioriIdTcode.query.filter_by(tcode=tcode_buscado).all()
    print(f"   Filas: {len(rows)}")
    for r in rows:
        print(f"   cat: {r.catalog_or_group_id!r}  titulo: {r.titulo!r}")

    # 1b. Columnas reales de sap_fiori_app_reg
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(db.engine)
    cols_reg = [c["name"] for c in insp.get_columns("sap_fiori_app_reg")]
    tiene_adid = "adid" in cols_reg
    print(f"\n   sap_fiori_app_reg columnas: {cols_reg}")

    # 2. ¿Está en SapFioriAppReg (SUI_TM_MM_APP)?
    sep(f"2. SapFioriAppReg para {tcode_buscado}")
    rows = SapFioriAppReg.query.filter_by(tcode=tcode_buscado).all()
    print(f"   Filas: {len(rows)}")
    for r in rows:
        adid_val = getattr(r, "adid", "—") if tiene_adid else "columna ausente"
        print(f"   app_id: {r.app_id!r}  adid: {adid_val!r}  sem_obj: {r.semantic_object!r}  titulo: {r.titulo!r}")

    # Objeto semántico conocido
    sem_known = None
    if rows:
        sem_known = rows[0].semantic_object

    # 3. SapFioriAppReg total + cuántas tienen adid no vacío
    total_app_reg = SapFioriAppReg.query.count()
    print(f"\n   Total SapFioriAppReg: {total_app_reg} filas")
    if tiene_adid:
        from sqlalchemy import func
        con_adid = db.session.query(func.count()).filter(
            SapFioriAppReg.adid != "", SapFioriAppReg.adid != None  # noqa: E711
        ).scalar()
        print(f"   Filas con adid no vacío: {con_adid}")
        # Muestra las primeras 3 para ver el formato
        sample = SapFioriAppReg.query.filter(
            SapFioriAppReg.adid != "", SapFioriAppReg.adid != None  # noqa: E711
        ).limit(3).all()
        for s in sample:
            print(f"   sample adid={s.adid!r} tcode={s.tcode!r}")

    # 4. SapChipCatalog: buscar chips que podrían resolverse a este tcode
    sep(f"3. SapChipCatalog - chips relacionados con {tcode_buscado}")

    all_chips = SapChipCatalog.query.all()
    print(f"   Total chips en DB: {len(all_chips)}")

    matching = []
    for chip in all_chips:
        cfg = chip.config_raw or ""
        if tcode_buscado in cfg.upper():
            matching.append(("tcode_en_config", chip))
        elif sem_known and sem_known.lower() in cfg.lower():
            matching.append(("sem_obj_en_config", chip))
        elif chip.component_id and tcode_buscado in chip.component_id.upper():
            matching.append(("component_id_match", chip))

    print(f"   Chips con {tcode_buscado!r} o sem_obj en config: {len(matching)}")
    for reason, chip in matching[:5]:
        print(f"   [{reason}] cat: {chip.catalog_or_group_id!r}  comp: {chip.component_id!r}")
        print(f"            config: {(chip.config_raw or '')[:120]!r}")

    # 5. Chips de ZSD_CAT_FACTURAS_2 específicamente
    sep("4. SapChipCatalog de ZSD_CAT_FACTURAS_2")
    chips = SapChipCatalog.query.filter_by(catalog_or_group_id="ZSD_CAT_FACTURAS_2").all()
    print(f"   Chips: {len(chips)}")
    for c in chips:
        # Intentar parsear el JSON para ver transaction.code y semantic_object
        tcode_json = sem_obj_json = titulo_json = ""
        try:
            outer = json.loads(c.config_raw or "")
            inner = json.loads(outer.get("tileConfiguration", "{}"))
            tcode_json = (inner.get("transaction") or {}).get("code", "")
            sem_obj_json = inner.get("navigation_semantic_object") or inner.get("semantic_object", "")
            titulo_json = inner.get("display_title_text", "")
        except Exception:
            pass
        print(f"   comp_id: {c.component_id!r}")
        print(f"     tcode_json: {tcode_json!r}  sem_obj: {sem_obj_json!r}  titulo: {titulo_json!r}")
        if not tcode_json and not sem_obj_json:
            print(f"     config_raw: {(c.config_raw or '')[:100]!r}")

    # 6. AGR_BUFFI y AGR_HIER para ZSD_FACTURAS
    sep("5. AGR_BUFFI / AGR_HIER para ZSD_FACTURAS")
    buffi = SapBuffiUrl.query.filter_by(role_name="ZSD_FACTURAS").all()
    hier  = SapRoleHierNode.query.filter_by(role_name="ZSD_FACTURAS").all()
    print(f"   SapBuffiUrl:    {len(buffi)} filas")
    for b in buffi[:5]:
        print(f"     contador: {b.contador!r}  url: {(b.url or '')[:80]!r}")
    print(f"   SapRoleHierNode: {len(hier)} filas")

    # 7. SapRoleTcode para F0797
    sep(f"6. SapRoleTcode para {tcode_buscado}")
    rt = SapRoleTcode.query.filter_by(tcode=tcode_buscado).all()
    print(f"   Filas: {len(rt)}")
    for r in rt:
        print(f"   role: {r.role_name!r}  source: {r.source!r}")

    sep("RESUMEN")
    ok1 = SapFioriIdTcode.query.filter_by(tcode=tcode_buscado).count() > 0
    ok2 = SapFioriAppReg.query.filter_by(tcode=tcode_buscado).count() > 0
    ok3 = SapChipCatalog.query.filter_by(catalog_or_group_id="ZSD_CAT_FACTURAS_2").count() > 0
    ok4 = SapBuffiUrl.query.filter_by(role_name="ZSD_FACTURAS").count() > 0
    ok5 = SapRoleHierNode.query.filter_by(role_name="ZSD_FACTURAS").count() > 0
    print(f"  SapFioriIdTcode resuelto:     {'SI' if ok1 else 'NO - falta recompute o datos'}")
    print(f"  SapFioriAppReg (SUI_TM_MM):   {'SI' if ok2 else 'NO - importar SUI_TM_MM_APP'}")
    print(f"  Chips de ZSD_CAT_FACTURAS_2:  {'SI' if ok3 else 'NO - importar PB_C_CHIPM'}")
    print(f"  AGR_BUFFI para ZSD_FACTURAS:  {'SI' if ok4 else 'NO - importar AGR_BUFFI'}")
    print(f"  AGR_HIER  para ZSD_FACTURAS:  {'SI' if ok5 else 'NO - importar AGR_HIER'}")
