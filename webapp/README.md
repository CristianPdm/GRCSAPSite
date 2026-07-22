# Sitio de Control de Licencias SAP y GRC - Grupo Simpa

Esqueleto inicial del sitio multiusuario en Python (Flask) + SQLite que
reemplaza a la app de un solo archivo `SAP_SOD_Analyzer_v640.html`.

Esta primera entrega incluye:

- Login y sesiones (Flask-Login), contrasenas con hash (Werkzeug).
- Roles con permisos configurables por modulo (tabla `roles`), no solo
  los 4 roles iniciales: un Administrador puede crear roles nuevos desde
  la pantalla "Roles" marcando los permisos que necesite.
- Gestion de usuarios (alta, edicion, habilitar/deshabilitar, asignar rol).
- Registro de auditoria (login, cambios de usuarios/roles).
- Paginas placeholder para los modulos "SOD/GRC" y "Licencias SAP",
  protegidas por permiso, listas para recibir la migracion de la
  funcionalidad real de `SAP_SOD_Analyzer_v640.html` en la siguiente
  iteracion (importacion de tablas SAP, motor de reglas, matriz,
  excepciones, reportes Word/Excel).
- CSS organizado en `static/css/base.css` (variables, reset, layout
  global) + un archivo `.module.css` por componente en
  `static/css/components/`, sin estilos en linea ni Tailwind, segun lo
  pedido para este proyecto.

## Roles iniciales

| Rol                     | Permisos |
|--------------------------|----------|
| Administrador            | Todo (usuarios, roles, config SOD, licencias, auditoria) |
| Auditor                  | Ver SOD/GRC, ejecutar analisis, exportar reportes, ver auditoria |
| Visualizador             | Solo lectura de SOD/GRC y licencias |
| Consultor de Licencias   | Ver y administrar solo el modulo de licencias SAP |

## Instalacion (Windows / PowerShell)

```powershell
cd webapp
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Si PowerShell bloquea la ejecucion del script de activacion, usar en su
lugar `cmd.exe` con `.venv\Scripts\activate.bat`, o ejecutar una vez:
`Set-ExecutionPolicy -Scope Process RemoteSigned`.

## Instalacion (Linux / macOS)

```bash
cd webapp
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Primer arranque (crear base de datos y usuario administrador)

```
python seed.py
```

Te pedira un usuario, nombre completo y contrasena para la cuenta de
Administrador inicial. Esto solo es necesario la primera vez (si la base
de datos ya tiene usuarios, el script no crea ninguno nuevo).

## Ejecutar el sitio

```
python run.py
```

Por defecto queda disponible en `http://127.0.0.1:5000`. El entorno virtual
(`.venv`) debe estar activado en la terminal cada vez que se ejecute
`seed.py` o `run.py`; no es necesario reinstalar las dependencias en cada
sesion, solo volver a activarlo.

## Estructura

```
webapp/
  app/
    auth/        -> login / logout
    main/        -> panel principal (dashboard por rol)
    admin/       -> usuarios, roles, auditoria
    sod/         -> modulo SOD/GRC (placeholder, a migrar)
    licenses/    -> modulo Licencias SAP (placeholder, a migrar)
    models.py    -> Role, User, AuditLog (SQLAlchemy)
    decorators.py-> permission_required(...)
  templates/     -> HTML (Jinja2), una carpeta por modulo
  static/css/
    base.css            -> variables y estilos globales reutilizables
    components/*.module.css -> un archivo por componente/pagina
  instance/      -> aqui se crea grc_simpa.db (SQLite, no se versiona)
  seed.py        -> crea roles base + primer usuario administrador
  run.py         -> arranque de la app
```

## Siguientes pasos (proxima iteracion)

1. Migrar la importacion de tablas SAP (AGR_USERS, AGR_TCODES, AGR_1251,
   AGR_DEFINE, TSTCT) al modulo `app/sod`, guardando los datos en SQLite
   en vez de IndexedDB/sql.js.
2. Migrar el motor de reglas SOD, el gestor de matriz y las excepciones.
3. Migrar la generacion de reportes (Word/Excel) y el log de auditoria
   especifico del analisis SOD.
4. Migrar el calculo de licencias SAP (FUE_Rol.xlsx / FUE_Users.xlsx) al
   modulo `app/licenses`.
