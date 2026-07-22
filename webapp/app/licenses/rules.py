"""Constantes y clasificador del modelo de licenciamiento FUE (Full Use
Equivalent) de SAP, migradas tal cual de SAP_SOD_Analyzer_v640.html.

Tipos FUE (de mayor a menor "peso" de licencia):
  ADV  = GB Advanced Use      (peso 1.0   -> 1 FUE completo)
  CORE = GC Core Use          (peso 0.2   -> 1/5 de FUE)
  SELF = GD Self-Service Use  (peso 1/30  -> 1/30 de FUE)
  NONE = sin tipo / tecnico    (peso 0.0)
"""

FUE_WEIGHT = {
    "ADV": 1.0,
    "CORE": 0.2,
    "SELF": 1 / 30,
    "NONE": 0.0,
}

FUE_ORDER = {
    "ADV": 3,
    "CORE": 2,
    "SELF": 1,
    "NONE": 0,
}

FUE_LABEL = {
    "ADV": "GB Advanced Use",
    "CORE": "GC Core Use",
    "SELF": "GD Self-Service Use",
    "NONE": "Sin tipo FUE",
}

# Dias sin acceso a partir de los cuales un usuario se considera "inactivo"
# (usado para la alerta de usuarios con licencia pero sin uso reciente).
INACTIVITY_THRESHOLD_DAYS = 90


def classify_fue_type(raw_text):
    """Clasifica el texto crudo de tipo FUE (columna 'FUE' de FUE_Users.xlsx
    o columna sin nombre de FUE_Rol.xlsx) en uno de los 4 codigos internos.
    Coincidencia por substring, insensible a mayusculas -- igual que el
    clasificador original."""
    if not raw_text:
        return "NONE"
    text = str(raw_text).strip().upper()
    if not text:
        return "NONE"
    if "ADVANCED" in text:
        return "ADV"
    if "CORE" in text:
        return "CORE"
    if "SELF" in text:
        return "SELF"
    return "NONE"
