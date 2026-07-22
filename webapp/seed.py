"""Script de inicializacion: crea las tablas y los 4 roles base.

Tambien crea un usuario administrador inicial si todavia no existe ninguno.
Ejecutar una sola vez (o cuando se borre la base de datos):

    python seed.py
"""
import getpass

from app import create_app
from app.extensions import db
from app.models import Role, User
from app.sod.models import SodRule
from app.sod.rules_data import SOD_BASE_RULES

ROLES_BASE = [
    {
        "name": "Administrador",
        "description": "Acceso total: usuarios, roles, configuración SOD y licencias.",
        "is_system": True,
        "can_manage_users": True,
        "can_manage_roles": True,
        "can_view_sod": True,
        "can_run_sod_analysis": True,
        "can_manage_sod_config": True,
        "can_export_reports": True,
        "can_view_licenses": True,
        "can_manage_licenses": True,
        "can_view_audit_log": True,
    },
    {
        "name": "Auditor",
        "description": "Solo lectura + ejecutar análisis SOD + exportar reportes.",
        "is_system": True,
        "can_view_sod": True,
        "can_run_sod_analysis": True,
        "can_export_reports": True,
        "can_view_audit_log": True,
    },
    {
        "name": "Visualizador",
        "description": "Solo lectura de dashboards y resultados, sin exportar ni ejecutar.",
        "is_system": True,
        "can_view_sod": True,
        "can_view_licenses": True,
    },
    {
        "name": "Consultor de Licencias",
        "description": "Solo el módulo de licencias SAP, sin acceso a SOD/GRC.",
        "is_system": True,
        "can_view_licenses": True,
        "can_manage_licenses": True,
    },
    {
        "name": "Importador SAP",
        "description": "Solo puede cargar los archivos Excel de SAP (SOD y Licencias). "
                        "No puede editar reglas, excepciones ni administrar licencias.",
        "is_system": True,
        "can_view_sod": True,
        "can_view_licenses": True,
        "can_import_sap_data": True,
    },
]


def run_seed():
    app = create_app()

    with app.app_context():
        db.create_all()

        roles_by_name = {}
        for data in ROLES_BASE:
            existing = Role.query.filter_by(name=data["name"]).first()
            if existing:
                roles_by_name[data["name"]] = existing
                continue
            role = Role(**data)
            db.session.add(role)
            roles_by_name[data["name"]] = role

        db.session.commit()
        print("Roles base creados/verificados:", ", ".join(roles_by_name.keys()))

        nuevas = 0
        for data in SOD_BASE_RULES:
            if SodRule.query.get(data["id"]):
                continue
            rule = SodRule(
                id=data["id"],
                modulo=data["modulo"],
                nivel=data["nivel"],
                descripcion=data["desc"],
                permiso1=data["p1"],
                permiso2=data["p2"],
                origen="BASE",
            )
            rule.set_tcodes1(data["t1"])
            rule.set_tcodes2(data["t2"])
            db.session.add(rule)
            nuevas += 1
        db.session.commit()
        print(f"Reglas SOD base: {nuevas} nuevas, {len(SOD_BASE_RULES) - nuevas} ya existian.")

        if User.query.count() == 0:
            print("\nNo hay usuarios. Vamos a crear el primer usuario Administrador.")
            username = input("Usuario (login): ").strip() or "admin"
            full_name = input("Nombre completo: ").strip()
            password = getpass.getpass("Contrasena: ")

            admin_user = User(
                username=username,
                full_name=full_name,
                role_id=roles_by_name["Administrador"].id,
            )
            admin_user.set_password(password or "admin123")
            db.session.add(admin_user)
            db.session.commit()
            print(f"Usuario '{username}' creado con rol Administrador.")
        else:
            print("Ya existen usuarios en la base de datos, no se crea ninguno nuevo.")


if __name__ == "__main__":
    run_seed()
