# Instrucciones y memoria del proyecto

Este archivo documenta las instrucciones del proyecto y las convenciones ya
establecidas en el código, para que cualquier persona (o asistente) que
continúe el trabajo las respete sin tener que redescubrirlas.

## Instrucciones originales del proyecto

> Crear un sitio en Python, con SQLite, con múltiples usuarios y diferentes
> tipos de roles para controlar las licencias de SAP y GRC.
>
> - Evitar usar estilos en línea o Tailwind directamente en los componentes.
> - Implementar una estructura donde se usen archivos CSS globales para
>   estilos reutilizables y CSS Modules para estilos individuales de cada
>   componente.

Contexto: Cris es el responsable SAP de Grupo Simpa SA, sobre una
instalación Private Cloud S/4HANA versión 2022. El sitio reemplaza a la
herramienta de un solo archivo `SAP_SOD_Analyzer_v640.html` y cubre dos
frentes: control de segregación de funciones (SOD/GRC) y control de
licenciamiento SAP (FUE).

## Convenciones de CSS (obligatorias)

- **Nunca** usar estilos en línea (`style="..."`) ni Tailwind.
- `static/css/base.css`: variables (tokens), reset y utilidades globales
  reutilizables entre pantallas.
- `static/css/components/<nombre>.module.css`: un archivo por
  componente/pantalla, con clases con prefijo propio del componente
  (ej. `rolesHeader__badges`, `mxTable__warnIcon`) para evitar colisiones.
- Los templates deben enlazar solo los `.module.css` que efectivamente usan.

## Convenciones de código

- Patrón *application factory* (`create_app()`) con Blueprints:
  `auth`, `main`, `admin`, `sod`, `licenses`, `rolesdb`, `chat`.
- Flask-SQLAlchemy + SQLite (`instance/grc_simpa.db`).
- Comentarios `{# QA: ... #}` (Jinja) o `# QA: ...` (Python) documentando
  **qué pedido del usuario** justifica cada cambio de código. Mantener esta
  práctica: facilita auditar por qué existe cada regla de negocio.
- Importadores de Excel (`app/sod/importers.py`, `app/licenses/importers.py`):
  patrón de "full refresh" — cada importador hace
  `Modelo.query.delete()` y vuelve a insertar todas las filas en cada
  importación, luego `db.session.commit()` y, si corresponde,
  `invalidate_analysis_cache()`.
- Lectura de Excel: `read_excel_matrix()` detecta la fila de encabezado y
  normaliza (sin tildes, mayúsculas); `column_index()` / `column_indices()`
  hacen matching tolerante por substring (soporta encabezados duplicados,
  ej. "Valor de la autorización" repetido en AGR_1251/AGR_1252).
- Guard `_table_exists(nombre_tabla)` (vía `sqlalchemy.inspect`) antes de
  consultar tablas que dependen de una migración que puede no haberse
  corrido todavía.
- Roles "hijos" válidos para consulta/filtrado: deben empezar con `Z` y no
  ser roles padre (compuestos). Este criterio se aplica de forma consistente
  en el picker de "Validar Asignación de Rol", en el módulo "Roles y
  Transacciones" y en la "Matriz Usuario x Rol x TCode".
- Tipos de FUE: `ADV`, `CORE`, `SELF`, `NONE` (`FUE_LABEL`), calculados a
  nivel de usuario (`LicenseUser`, fuente canónica) y también disponibles a
  nivel de rol (`LicenseRole`, referencia/auditoría).
- Migraciones: scripts sueltos en la raíz (`migrate_add_*.py`) que corren
  `db.create_all()` o alteran columnas puntuales — no hay Alembic. Correr
  manualmente después de actualizar el código cuando agregan tablas/columnas
  nuevas.

## Versionado y Git

- Versión de la app en `config.py` → `APP_VERSION`.
- Historial de tags: `v1.0`, `v2.1`, `v2.2`, `v3.0`.
- Repositorio remoto: `https://github.com/CristianPdm/GRCSAPSite.git`
  (rama `master`).
- El entorno de trabajo asistido (sandbox) no tiene credenciales guardadas
  para hacer `git push`; los commits/tags se preparan ahí pero el push a
  GitHub lo debe ejecutar Cris desde su equipo (`git push origin master`,
  `git push origin --tags`), o autorizando el conector de GitHub.

## Pendiente conocido

- Soportar referencias `PAGE` en `PB_C_CHIPM` y `CATID` en el recompute de
  tcodes/apps Fiori (backlog, no bloqueante).
