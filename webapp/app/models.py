from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


class Role(db.Model):
    """Rol de usuario con permisos por modulo.

    El sitio nace con 4 roles (Administrador, Auditor, Visualizador,
    Consultor de Licencias) pero un Administrador puede crear roles
    adicionales desde la pantalla de gestion de roles, marcando los
    permisos que necesite. Por eso los permisos son columnas booleanas
    en vez de estar fijos en el codigo.
    """

    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), unique=True, nullable=False)
    description = db.Column(db.String(255), default="")
    is_system = db.Column(db.Boolean, default=False)  # rol base, no se puede borrar

    # --- Permisos: administracion ---
    can_manage_users = db.Column(db.Boolean, default=False)
    can_manage_roles = db.Column(db.Boolean, default=False)

    # --- Permisos: modulo SOD / GRC ---
    can_view_sod = db.Column(db.Boolean, default=False)
    can_run_sod_analysis = db.Column(db.Boolean, default=False)
    can_manage_sod_config = db.Column(db.Boolean, default=False)  # reglas, matriz, importacion
    can_export_reports = db.Column(db.Boolean, default=False)

    # --- Permisos: modulo Licencias SAP ---
    can_view_licenses = db.Column(db.Boolean, default=False)
    can_manage_licenses = db.Column(db.Boolean, default=False)

    # --- Permisos: importacion de datos SAP ---
    # Independiente de can_manage_sod_config / can_manage_licenses: permite
    # crear un rol que SOLO pueda cargar los archivos Excel de SAP (sin
    # poder tocar reglas SOD, excepciones ni administracion de licencias).
    can_import_sap_data = db.Column(db.Boolean, default=False)

    # --- Permisos: modulo Roles y Transacciones (RolesDB) ---
    can_view_rolesdb = db.Column(db.Boolean, default=False)

    # --- Permisos: asistente IA (Chat) ---
    can_view_chat = db.Column(db.Boolean, default=False)

    # --- Permisos: auditoria ---
    can_view_audit_log = db.Column(db.Boolean, default=False)

    users = db.relationship("User", back_populates="role")

    def permission_labels(self):
        """Lista de permisos activos en lenguaje natural, para mostrarlos en la UI."""
        labels = {
            "can_manage_users": "Gestionar usuarios",
            "can_manage_roles": "Gestionar roles",
            "can_view_sod": "Ver módulo SOD/GRC",
            "can_run_sod_analysis": "Ejecutar análisis SOD",
            "can_manage_sod_config": "Administrar reglas/importación SOD",
            "can_export_reports": "Exportar reportes",
            "can_view_licenses": "Ver licencias SAP",
            "can_manage_licenses": "Administrar licencias SAP",
            "can_import_sap_data": "Importar datos SAP (SOD y Licencias)",
            "can_view_rolesdb": "Ver Roles y Transacciones SAP",
            "can_view_chat": "Usar asistente IA",
            "can_view_audit_log": "Ver auditoría",
        }
        return [label for attr, label in labels.items() if getattr(self, attr)]

    def __repr__(self):
        return f"<Role {self.name}>"


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    full_name = db.Column(db.String(150), nullable=False, default="")
    # No es unique a nivel de base: dos cuentas pueden compartir el mismo
    # mail (ej. una casilla de mesa de ayuda usada por varios usuarios
    # tecnicos). Se permite, pero se avisa con un warning al guardar (ver
    # app/admin/routes.py) para que quede claro que no es la unica cuenta
    # con ese mail.
    email = db.Column(db.String(150), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active_user = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)

    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=False)
    role = db.relationship("Role", back_populates="users")

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    # Flask-Login usa is_active para bloquear el login de cuentas deshabilitadas
    @property
    def is_active(self):
        return self.is_active_user

    def has_permission(self, permission_name):
        if not self.role:
            return False
        return bool(getattr(self.role, permission_name, False))

    def __repr__(self):
        return f"<User {self.username}>"


class AppSetting(db.Model):
    """Configuracion simple clave/valor editable desde la UI (sin tocar
    config.py ni variables de entorno). Pensada para valores que un
    administrador funcional necesita poder cambiar el mismo, como la
    carpeta del servidor donde se dejan los Excel exportados de SAP para
    la importacion en lote (ver app/utils/sap_import.py)."""

    __tablename__ = "app_settings"

    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.String(500), default="")

    @staticmethod
    def get(key, default=""):
        setting = AppSetting.query.get(key)
        return setting.value if setting and setting.value is not None else default

    @staticmethod
    def set(key, value):
        setting = AppSetting.query.get(key)
        if setting is None:
            setting = AppSetting(key=key, value=value)
            db.session.add(setting)
        else:
            setting.value = value
        db.session.commit()


class AuditLog(db.Model):
    """Registro simple de acciones relevantes (login, altas/bajas de usuarios, cambios de rol)."""

    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    username = db.Column(db.String(80), nullable=True)  # se guarda como texto por si el usuario se borra
    action = db.Column(db.String(120), nullable=False)
    details = db.Column(db.String(500), default="")
    ip_address = db.Column(db.String(45), nullable=True)

    @staticmethod
    def log(action, username=None, details="", ip_address=None):
        entry = AuditLog(action=action, username=username, details=details, ip_address=ip_address)
        db.session.add(entry)
        db.session.commit()
        return entry
