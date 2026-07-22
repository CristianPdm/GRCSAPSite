"""Calculo del consumo de licencias FUE (Full Use Equivalent) de SAP.

Usa el tipo FUE oficial por usuario (FUE_Users.xlsx, tabla LicenseUser) como
fuente canonica -- es la que SAP factura. El tipo FUE a nivel de rol
(FUE_Rol.xlsx, tabla LicenseRole) queda como referencia/auditoria.

build_fue_comparison() y compute_fue_optimization() cruzan ese FUE oficial
con el FUE derivado de los roles activos (build_user_risk(), del modulo
SOD). Esta es la unica direccion en la que Licencias lee de SOD -- al
reves de la convencion general (SOD lee de Licencias) porque esta
comparativa es, por definicion, propia del modulo de Licencias. El import
se hace de forma local/perezosa dentro de cada funcion para evitar un
ciclo de import a nivel de modulo entre app.sod y app.licenses."""
from datetime import date

from app.licenses.models import LicenseUser
from app.licenses.rules import FUE_LABEL, FUE_ORDER, FUE_WEIGHT, INACTIVITY_THRESHOLD_DAYS


def get_license_summary():
    """Resumen para el tile del dashboard: total de FUEs consumidos,
    desglose por tipo y usuarios con licencia pero sin uso reciente.

    Nombres de campo (`advanced`/`core`/`self_service`/`unassigned`/
    `total_fue`) elegidos para coincidir exactamente con lo que espera
    templates/main/dashboard.html."""
    users = LicenseUser.query.all()

    if not users:
        return {
            "has_data": False,
            "total_users": 0,
            "advanced": 0,
            "core": 0,
            "self_service": 0,
            "unassigned": 0,
            "total_fue": 0,
            "inactive_licensed": 0,
        }

    by_type = {code: 0 for code in FUE_WEIGHT}
    total_fue = 0.0
    today = date.today()
    inactive_licensed = 0

    for user in users:
        code = user.fue_type_code if user.fue_type_code in FUE_WEIGHT else "NONE"
        by_type[code] += 1
        total_fue += FUE_WEIGHT[code]

        if code != "NONE" and user.last_access:
            if (today - user.last_access).days > INACTIVITY_THRESHOLD_DAYS:
                inactive_licensed += 1

    return {
        "has_data": True,
        "total_users": len(users),
        "advanced": by_type["ADV"],
        "core": by_type["CORE"],
        "self_service": by_type["SELF"],
        "unassigned": by_type["NONE"],
        "total_fue": round(total_fue, 2),
        "by_type_label": FUE_LABEL,
        "inactive_licensed": inactive_licensed,
    }


def build_fue_comparison():
    """Equivalente a rFUE() del original: por cada usuario que tiene FUE
    oficial (LicenseUser) y/o roles activos, compara el tipo FUE que SAP
    factura contra el tipo FUE que le correspondería segun sus roles
    (build_user_risk(), del modulo SOD). Devuelve una fila por usuario,
    con bandera is_diff/dif_type para que la plantilla pueda mostrar
    "todos" o filtrar a "solo diferencias" del lado del cliente."""
    from app.sod.engine import build_user_risk

    user_risk = {row["user"]: row for row in build_user_risk()}
    license_users = LicenseUser.query.all()
    license_by_name = {u.username: u for u in license_users}

    usernames = set(license_by_name.keys()) | set(user_risk.keys())

    rows = []
    for username in usernames:
        lic = license_by_name.get(username)
        ur = user_risk.get(username)

        sap_code = lic.fue_type_code if (lic and lic.fue_type_code in FUE_WEIGHT) else "NONE"
        sap_label = (lic.fue_type_raw if lic and lic.fue_type_raw else "") or FUE_LABEL.get(sap_code, "Sin tipo FUE")
        role_code = ur["fue_type"] if ur else "NONE"
        sap_weight = FUE_WEIGHT.get(sap_code, 0.0)
        role_weight = FUE_WEIGHT.get(role_code, 0.0)
        delta = sap_weight - role_weight
        is_diff = sap_code != role_code

        dif_type = None
        if is_diff:
            if not ur or role_code == "NONE":
                dif_type = "noroles"
            elif delta > 0:
                dif_type = "over"
            else:
                dif_type = "under"

        rows.append({
            "user": username,
            "nombre": (lic.full_name if lic else "") or (ur["nombre"] if ur else ""),
            "sap_code": sap_code,
            "sap_label": sap_label,
            "has_sap_fue": lic is not None,
            "role_code": role_code,
            "role_label": FUE_LABEL.get(role_code, "Sin tipo FUE"),
            "has_roles": ur is not None,
            "fue_roles": ur["fue_roles"] if ur else [],
            "is_diff": is_diff,
            "dif_type": dif_type,
        })

    rows.sort(key=lambda r: (-FUE_ORDER.get(r["sap_code"], 0), r["user"]))
    return rows


def compute_fue_optimization():
    """Equivalente a computeFUEOpt() del original: candidatos a baja de
    licencia (usuarios inactivos hace mas de INACTIVITY_THRESHOLD_DAYS
    dias que todavia tienen un FUE pagado) y candidatos a downgrade
    (FUE oficial mayor al que les corresponderia segun sus roles activos),
    con el ahorro estimado en FUE de cada cambio."""
    from app.sod.engine import build_user_risk

    user_risk = {row["user"]: row for row in build_user_risk()}
    license_users = LicenseUser.query.all()

    inactive = []
    downgrade = []
    today = date.today()

    for lic in license_users:
        sap_code = lic.fue_type_code if lic.fue_type_code in FUE_WEIGHT else "NONE"
        sap_weight = FUE_WEIGHT.get(sap_code, 0.0)
        days = (today - lic.last_access).days if lic.last_access else None
        ur = user_risk.get(lic.username)
        role_code = ur["fue_type"] if ur else "NONE"

        if days is not None and days > INACTIVITY_THRESHOLD_DAYS and sap_weight > 0:
            inactive.append({
                "user": lic.username,
                "nombre": lic.full_name or (ur["nombre"] if ur else ""),
                "fue_type": lic.fue_type_raw or FUE_LABEL.get(sap_code, ""),
                "fue_code": sap_code,
                "fue_weight": sap_weight,
                "days": days,
                "last_access": lic.last_access,
                "mx": ur["mx"] if ur else "SIN",
                "has_roles": ur is not None,
                "cc": ur["cc"] if ur else 0,
            })

        sap_order = FUE_ORDER.get(sap_code, 0)
        role_order = FUE_ORDER.get(role_code, 0)
        if sap_order > 1 and role_order < sap_order:
            suggested_code = "CORE" if role_code == "CORE" else "SELF"
            suggested_weight = FUE_WEIGHT[suggested_code]
            downgrade.append({
                "user": lic.username,
                "nombre": lic.full_name or (ur["nombre"] if ur else ""),
                "current_fue": lic.fue_type_raw or FUE_LABEL.get(sap_code, ""),
                "current_weight": sap_weight,
                "derived_code": role_code,
                "derived_label": FUE_LABEL.get(role_code, "Sin tipo FUE"),
                "suggested_fue": FUE_LABEL[suggested_code],
                "suggested_weight": suggested_weight,
                "savings": sap_weight - suggested_weight,
            })

    inactive.sort(key=lambda u: -u["days"])
    downgrade.sort(key=lambda u: -u["savings"])

    total_inactive_savings = sum(u["fue_weight"] for u in inactive)
    total_downgrade_savings = sum(u["savings"] for u in downgrade)

    return {
        "inactive": inactive,
        "downgrade": downgrade,
        "total_inactive_savings": total_inactive_savings,
        "total_downgrade_savings": total_downgrade_savings,
        "total_savings": total_inactive_savings + total_downgrade_savings,
    }
