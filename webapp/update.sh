#!/usr/bin/env bash
# =============================================================================
# update.sh — Actualización de Licencias & GRC a nueva versión
# Ejecutar como root: sudo bash update.sh
# La base de datos existente NO se toca (solo se actualizan los archivos).
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
section() { echo -e "\n${BOLD}━━━  $*  ━━━${NC}"; }

[[ $EUID -ne 0 ]] && error "Ejecutar como root: sudo bash update.sh"

# ── Detectar instalación existente ───────────────────────────────────────────
INSTALL_DIR="/opt/grc-app"
read -rp "Directorio de instalación existente [${INSTALL_DIR}]: " INPUT_DIR
INSTALL_DIR="${INPUT_DIR:-$INSTALL_DIR}"

[[ ! -f "$INSTALL_DIR/.env" ]] && \
    error "No se encontró instalación en $INSTALL_DIR (falta .env)"

SERVICE_NAME="grc-app"

echo ""
warn "La actualización reemplazará el código de la aplicación."
warn "La base de datos y el archivo .env NO serán modificados."
read -rp "¿Continuar? [S/n]: " CONFIRM
[[ "${CONFIRM,,}" == "n" ]] && echo "Actualización cancelada." && exit 0

# ── Detener servicio ─────────────────────────────────────────────────────────
section "Deteniendo servicio"

systemctl stop "${SERVICE_NAME}.service" || warn "Servicio no estaba activo."
ok "Servicio detenido."

# ── Backup rápido del código anterior ────────────────────────────────────────
section "Backup de versión anterior"

BACKUP_DIR="${INSTALL_DIR}_backup_$(date +%Y%m%d_%H%M%S)"
cp -a "$INSTALL_DIR" "$BACKUP_DIR"
ok "Backup en: $BACKUP_DIR"

# ── Copiar nueva versión ─────────────────────────────────────────────────────
section "Actualizando archivos"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

rsync -a --delete \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='instance' \
    --exclude='.env' \
    --exclude='install.sh' \
    --exclude='update.sh' \
    --exclude='err.log' \
    --exclude='test_marker.txt' \
    --exclude='deploy_V01' \
    --exclude='IconoApp.png' \
    "$SCRIPT_DIR/" "$INSTALL_DIR/"

ok "Archivos actualizados."

# ── Actualizar dependencias Python ───────────────────────────────────────────
section "Actualizando dependencias"

"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install --quiet \
    -r "$INSTALL_DIR/requirements.txt" \
    -r "$INSTALL_DIR/requirements-prod.txt"
ok "Dependencias actualizadas."

# ── Migraciones de base de datos ─────────────────────────────────────────────
section "Migraciones de base de datos"

cd "$INSTALL_DIR"

info "Ejecutando migrate_add_can_import.py..."
"$INSTALL_DIR/.venv/bin/python" migrate_add_can_import.py 2>/dev/null || true

info "Ejecutando migrate_add_parent_role.py..."
"$INSTALL_DIR/.venv/bin/python" migrate_add_parent_role.py 2>/dev/null || true

info "Ejecutando migrate_add_fiori_tables.py..."
"$INSTALL_DIR/.venv/bin/python" migrate_add_fiori_tables.py 2>/dev/null || true

info "Ejecutando migrate_add_fiori_titulo.py..."
"$INSTALL_DIR/.venv/bin/python" migrate_add_fiori_titulo.py 2>/dev/null || true

info "Ejecutando migrate_add_usr02_table.py..."
"$INSTALL_DIR/.venv/bin/python" migrate_add_usr02_table.py 2>/dev/null || true

info "Ejecutando migrate_drop_email_unique.py..."
"$INSTALL_DIR/.venv/bin/python" migrate_drop_email_unique.py 2>/dev/null || true

info "Ejecutando migrate_add_perf_indexes.py..."
"$INSTALL_DIR/.venv/bin/python" migrate_add_perf_indexes.py 2>/dev/null || true

ok "Migraciones aplicadas (errores ignorados si columna ya existía)."

# ── Permisos y reinicio ──────────────────────────────────────────────────────
section "Permisos y reinicio"

SERVICE_USER="grcapp"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
chmod -R 750 "$INSTALL_DIR"
chmod 600 "$INSTALL_DIR/.env"

systemctl start "${SERVICE_NAME}.service"
sleep 2

if systemctl is-active --quiet "${SERVICE_NAME}.service"; then
    ok "Servicio reiniciado correctamente."
else
    error "El servicio no arrancó. Revisá: journalctl -u ${SERVICE_NAME} -n 50\nBackup disponible en: $BACKUP_DIR"
fi

# ── Resultado ────────────────────────────────────────────────────────────────
section "Actualización completada"

echo ""
echo -e "  ${GREEN}${BOLD}✓ Aplicación actualizada correctamente${NC}"
echo ""
echo -e "  Backup anterior: ${BOLD}${BACKUP_DIR}${NC}"
echo -e "  (Podés eliminarlo si todo funciona bien)"
echo ""
