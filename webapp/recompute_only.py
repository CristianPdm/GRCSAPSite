"""Vuelve a resolver tcodes Fiori sin reimportar archivos.
Uso: cd webapp && python recompute_only.py
"""
from app import create_app
from app.sod.importers import recompute_fiori_tcodes

app = create_app()
with app.app_context():
    saved = recompute_fiori_tcodes()
    print(f"OK: {saved} entradas en SapFioriIdTcode tras recompute")
