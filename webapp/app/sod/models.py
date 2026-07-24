"""Modelos de datos del modulo SOD/GRC.

Reemplazan, con columnas tipadas, al esquema original de
SAP_SOD_Importer_v2.html / SAP_SOD_Analyzer_v640.html, que guardaba cada
fila importada como un blob JSON generico (tabla `(id, data TEXT)`).
"""
import json
from datetime import datetime

from app.extensions import db


class SapRoleAssignment(db.Model):
    """Asignacion rol -> usuario, importada de AGR_USERS.xlsx."""

    __tablename__ = "sap_role_assignments"

    id = db.Column(db.Integer, primary_key=True)
    role_name = db.Column(db.String(80), nullable=False, index=True)
    username = db.Column(db.String(40), nullable=False, index=True)
    valid_to = db.Column(db.Date, nullable=True, index=True)  # "Fecha fin"; None/9999 = vigente
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)


class SapRoleTcode(db.Model):
    """Transaccion habilitada por un rol, importada de AGR_1251.xlsx
    (objeto S_TCODE, preferido) o AGR_TCODES.xlsx (alternativa/respaldo)."""

    __tablename__ = "sap_role_tcodes"

    id = db.Column(db.Integer, primary_key=True)
    role_name = db.Column(db.String(80), nullable=False, index=True)
    tcode = db.Column(db.String(40), nullable=False, index=True)
    source = db.Column(db.String(20), default="AGR_1251")  # AGR_1251 | AGR_TCODES
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)


class SapRoleDescription(db.Model):
    """Descripcion de rol, importada de AGR_DEFINE.xlsx. Tambien guarda la
    jerarquia rol simple -> rol compuesto: AGR_DEFINE trae el rol "hijo" en
    la primera columna y el rol "padre" (compuesto) en la segunda; si esa
    segunda columna viene vacia, el rol no pertenece a ningun compuesto
    (es definitivo). Se usa en el motor SOD para expandir un rol compuesto
    asignado a un usuario a sus roles simples, que son los que realmente
    traen tcodes/apps Fiori en AGR_1251/AGR_TCODES/AGR_HIER."""

    __tablename__ = "sap_role_descriptions"

    id = db.Column(db.Integer, primary_key=True)
    role_name = db.Column(db.String(80), unique=True, nullable=False)
    description = db.Column(db.String(255), default="")
    parent_role = db.Column(db.String(80), nullable=True, index=True)
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)


class SapRoleTableAuth(db.Model):
    """Roles con autorizacion irrestricta a tablas (objeto S_TABU_DIS con
    valor '*'), detectados al importar AGR_1251.xlsx. Se usa como bandera
    sintetica de riesgo ALTO en la vista de Roles criticos (equivalente al
    flag 'S_TABU_DIS=*' del original)."""

    __tablename__ = "sap_role_table_auth"

    id = db.Column(db.Integer, primary_key=True)
    role_name = db.Column(db.String(80), unique=True, nullable=False, index=True)
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)


class SapTcodeDescription(db.Model):
    """Descripcion corta de cada transaccion SAP, importada de TSTCT.xlsx
    (solo informativa: se usa como tooltip/columna en Roles criticos y en
    la Matriz Usuario x Rol x TCode)."""

    __tablename__ = "sap_tcode_descriptions"

    id = db.Column(db.Integer, primary_key=True)
    tcode = db.Column(db.String(40), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255), default="")
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)


class SapRoleHierNode(db.Model):
    """Nodo de catalogo/grupo de negocio Fiori asignado a un rol, importado
    de AGR_HIER.xlsx (solo se guardan las filas Tp.report='OT' con Nombre
    ampliado CAT_PROVIDER o GROUP_PROVIDER; el resto de AGR_HIER -tcodes
    clasicos, carpetas de menu- ya lo cubren AGR_1251/AGR_TCODES).

    El campo `contador` ('Contador para ID menu' en el export) es la clave
    para unir con SapBuffiUrl y obtener la URL que trae el ID tecnico del
    catalogo/grupo (ver importers.recompute_fiori_tcodes)."""

    __tablename__ = "sap_role_hier_nodes"

    id = db.Column(db.Integer, primary_key=True)
    role_name = db.Column(db.String(80), nullable=False, index=True)
    contador = db.Column(db.String(20), nullable=False)
    kind = db.Column(db.String(20), nullable=False)  # CAT_PROVIDER | GROUP_PROVIDER
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)


class SapBuffiUrl(db.Model):
    """URL de un nodo de menu de un rol, importada de AGR_BUFFI.xlsx. Se
    une con SapRoleHierNode por (role_name, contador) para extraer el ID
    tecnico del catalogo/grupo Fiori embebido en la URL (formato
    'X-SAP-UI2-<TIPO>:<ID>?...')."""

    __tablename__ = "sap_buffi_urls"

    id = db.Column(db.Integer, primary_key=True)
    role_name = db.Column(db.String(80), nullable=False, index=True)
    contador = db.Column(db.String(20), nullable=False)
    url = db.Column(db.String(255), default="")
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.Index("ix_buffi_role_contador", "role_name", "contador"),)


class SapFioriIdTcode(db.Model):
    """Transaccion (tcode) que ofrece un catalogo o grupo Fiori, resuelta al
    importar PB_C_CHIPM.xlsx. Cada chip/tile del catalogo trae su
    configuracion (transaction.code directo, o el patron propio de Grupo
    Simpa Z_<TCODE>/Z<TCODE> para exponer transacciones clasicas en el
    Launchpad). Las apps Fiori puras OData (sin tcode equivalente, ej.
    'ProfitCenter') no generan fila aqui -- ver
    importers._extract_tcode_from_config.

    `titulo` guarda el 'display_title_text' del chip (titulo visible de la
    app Fiori en el Launchpad), cuando el archivo lo trae. Es solo un dato
    de apoyo: se usa como respaldo en el picker de TCodes (pantalla de
    reglas SOD) cuando TSTCT.xlsx no tiene descripcion para ese codigo --
    no participa del motor de deteccion de conflictos."""

    __tablename__ = "sap_fiori_id_tcodes"

    id = db.Column(db.Integer, primary_key=True)
    catalog_or_group_id = db.Column(db.String(80), nullable=False, index=True)
    tcode = db.Column(db.String(40), nullable=False)
    titulo = db.Column(db.String(120), default="")
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("catalog_or_group_id", "tcode", name="uq_fiori_id_tcode"),)


class SapChipCatalog(db.Model):
    """Staging de PB_C_CHIPM.xlsx: un registro por chip/tile de cada
    catálogo o grupo Fiori personalizado. Guarda los datos crudos con el
    component_id (CHIPID) preservado para que recompute_fiori_tcodes()
    pueda re-resolver tcodes usando SapFioriAppReg (SUI_TM_MM_APP) sin
    necesidad de re-importar PB_C_CHIPM."""

    __tablename__ = "sap_chip_catalog"

    id = db.Column(db.Integer, primary_key=True)
    catalog_or_group_id = db.Column(db.String(80), nullable=False, index=True)
    component_id = db.Column(db.String(80), default="", index=True)  # CHIPID de PB_C_CHIPM
    config_raw = db.Column(db.Text, default="")                      # CONFIGURATION crudo
    # Catálogo fuente cuando col-6 es X-SAP-UI2-PAGE:...:SRC_CATALOG:SLOT
    page_ref_catalog = db.Column(db.String(120), default="", index=True)
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)


class SapFioriAppReg(db.Model):
    """Registro de apps Fiori importado de SUI_TM_MM_APP.xlsx.

    Mapea component_id (app/chip ID) → tcode de manera estructurada,
    sin depender del parsing de JSON de PB_C_CHIPM. Se usa como fuente
    primaria en recompute_fiori_tcodes(): si el JSON config del chip no
    trae transaction.code, se busca el component_id aquí antes de aplicar
    la heurística de objeto semántico."""

    __tablename__ = "sap_fiori_app_reg"

    id = db.Column(db.Integer, primary_key=True)
    app_id = db.Column(db.String(80), nullable=False, index=True)  # ID Fiori corto (ej. "F0797")
    adid = db.Column(db.String(80), default="", index=True)        # App Descriptor ID (FA163EDF...)
    tcode = db.Column(db.String(40), nullable=False)
    titulo = db.Column(db.String(200), default="")
    semantic_object = db.Column(db.String(80), default="")
    # Catálogo nativo SAP de la app (ej. SAP_TC_CEC_SD_COMMON), de SUI_TM_MM_APP col "ID de catálogo técnico"
    catid = db.Column(db.String(120), default="", index=True)
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("app_id", "tcode", name="uq_fiori_app_reg"),)


class SapUserStatus(db.Model):
    """Estado del usuario SAP a nivel de cuenta (no de rol), importado de
    USR02.xlsx (tabla USR02 estandar): tipo de usuario, status de bloqueo,
    intentos fallidos de acceso, vigencia de la cuenta y fecha del ultimo
    login real al sistema.

    Es complementario al `last_access` de LicenseUser (FUE_Users.xlsx,
    modulo Licencias): ese viene de un reporte de licenciamiento, mientras
    que `ultimo_login` aqui viene directo de la tabla maestra de usuarios
    SAP, y `bloqueado` permite distinguir un usuario inactivo de uno que
    en realidad ya esta deshabilitado (no es un riesgo real de SOD activo,
    aunque conserve roles/conflictos asignados)."""

    __tablename__ = "sap_user_status"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False, index=True)
    tipo_usuario = db.Column(db.String(40), default="")
    bloqueado = db.Column(db.Boolean, default=False)
    lock_status_raw = db.Column(db.String(10), default="")
    intentos_fallidos = db.Column(db.Integer, default=0)
    valido_desde = db.Column(db.Date, nullable=True)
    valido_hasta = db.Column(db.Date, nullable=True)
    ultimo_login = db.Column(db.Date, nullable=True)
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)

    _LOCK_LABELS = {
        "0": "No bloqueado",
        "32": "Bloqueado por intentos fallidos",
        "64": "Bloqueado por el administrador",
        "128": "Bloqueado (CUA)",
        "192": "Bloqueado por administrador y CUA",
    }

    @property
    def lock_label(self):
        """Texto legible del status de bloqueo (campo UFLAG de USR02). Los
        codigos no listados explicitamente (combinaciones menos frecuentes)
        se muestran de forma generica como 'Bloqueado', ya que en USR02
        cualquier valor distinto de 0 implica que el usuario esta
        bloqueado."""
        if self.lock_status_raw in self._LOCK_LABELS:
            return self._LOCK_LABELS[self.lock_status_raw]
        return "Bloqueado" if self.bloqueado else "No bloqueado"


class SapRoleOrgLevel(db.Model):
    """Valores de nivel organizacional (objetos de autorizacion con
    variable USVAR, ej. $WERKS, $BUKRS) asignados a un rol, importados de
    AGR_1252.xlsx. Definen a que areas de la organizacion (centro,
    sociedad, organizacion de compras, etc.) da acceso el rol, ademas de
    las transacciones propias del rol (AGR_1251/AGR_HIER).

    Un mismo nivel organizacional (`nivel_codigo`) puede repetirse varias
    veces para un rol -- una fila por cada valor autorizado, o por cada
    extremo de un rango (valor_bajo/valor_alto) -- y el valor puede venir
    vacio (nivel definido en el rol pero sin restriccion cargada)."""

    __tablename__ = "sap_role_org_levels"

    id = db.Column(db.Integer, primary_key=True)
    role_name = db.Column(db.String(80), nullable=False, index=True)
    nivel_codigo = db.Column(db.String(40), nullable=False)  # ej. '$WERKS', '$BUKRS'
    valor_bajo = db.Column(db.String(80), default="")
    valor_alto = db.Column(db.String(80), default="")
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.Index("ix_role_org_level_role", "role_name", "nivel_codigo"),)


class SapOrgLevelDesc(db.Model):
    """Descripcion de cada variable de nivel organizacional (columna
    'Variable' de USVAR.xlsx, ej. '$WERKS' -> 'Centro'), usada para
    mostrar un texto legible junto al codigo tecnico en el detalle de
    rol (ver SapRoleOrgLevel)."""

    __tablename__ = "sap_org_level_descriptions"

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(40), unique=True, nullable=False, index=True)
    descripcion = db.Column(db.String(120), default="")
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)


class SodRule(db.Model):
    """Regla de la matriz SOD (conflicto entre dos grupos de transacciones).

    Se siembra con las reglas base de SAP_SOD_Analyzer_v640.html (ver
    app/sod/rules_data.py) y puede editarse/desactivarse/ampliarse desde
    la UI sin tocar codigo -- igual que la tabla SOD_REGLAS del original.
    """

    __tablename__ = "sod_rules"

    id = db.Column(db.String(20), primary_key=True)  # ej. 'MM-001'
    modulo = db.Column(db.String(10), default="")
    nivel = db.Column(db.String(10), nullable=False)  # CRITICO | ALTO | MEDIO
    descripcion = db.Column(db.String(255), nullable=False)
    permiso1 = db.Column(db.String(120), default="")
    tcodes1 = db.Column(db.Text, default="[]")  # lista JSON de tcodes lado A
    permiso2 = db.Column(db.String(120), default="")
    tcodes2 = db.Column(db.Text, default="[]")  # lista JSON de tcodes lado B
    activo = db.Column(db.Boolean, default=True)
    origen = db.Column(db.String(20), default="BASE")  # BASE | CUSTOM
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def tcodes1_list(self):
        return json.loads(self.tcodes1 or "[]")

    def tcodes2_list(self):
        return json.loads(self.tcodes2 or "[]")

    def set_tcodes1(self, codes):
        self.tcodes1 = json.dumps([c.strip().upper() for c in codes if c.strip()])

    def set_tcodes2(self, codes):
        self.tcodes2 = json.dumps([c.strip().upper() for c in codes if c.strip()])


class SodException(db.Model):
    """Excepcion aceptada para un conflicto puntual rol/usuario (requiere
    justificacion). Mientras exista la excepcion, ese usuario deja de
    contar como conflicto activo para esa regla -- igual que SOD_EXCEPCIONES
    en el original (alli siempre quedaba con nivel_ajustado='EXCEPTUADO')."""

    __tablename__ = "sod_exceptions"

    id = db.Column(db.Integer, primary_key=True)
    regla_id = db.Column(db.String(20), db.ForeignKey("sod_rules.id"), nullable=False, index=True)
    usuario = db.Column(db.String(40), nullable=False)
    nivel_original = db.Column(db.String(10), nullable=False)
    motivo = db.Column(db.Text, nullable=False)
    creado_por = db.Column(db.String(80), default="")
    fecha = db.Column(db.DateTime, default=datetime.utcnow)

    regla = db.relationship("SodRule")

    __table_args__ = (db.UniqueConstraint("regla_id", "usuario", name="uq_sod_exception_regla_usuario"),)
