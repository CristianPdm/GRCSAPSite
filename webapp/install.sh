#!/usr/bin/env bash
# =============================================================================
# install.sh — Instalador de Licencias & GRC
# Compatible con Ubuntu 20.04 / 22.04 / Debian 11+
# Ejecutar como root: sudo bash install.sh
# =============================================================================
set -euo pipefail

# ── Colores ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
section() { echo -e "\n${BOLD}━━━  $*  ━━━${NC}"; }

# ── Verificar root ───────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && error "Ejecutar como root: sudo bash install.sh"

# ── Banner ───────────────────────────────────────────────────────────────────
echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════╗"
echo "  ║   Licencias & GRC  —  Instalador    ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${NC}"

# ── Parámetros de instalación ────────────────────────────────────────────────
section "Configuración"

read -rp "Nombre de la empresa (ej: Grupo Acme):           " EMPRESA
[[ -z "$EMPRESA" ]] && error "El nombre de empresa no puede estar vacío."

read -rp "Offset de zona horaria UTC (ej: -3 para ARG/URU): " TZ_OFFSET
TZ_OFFSET="${TZ_OFFSET:--3}"

INSTALL_DIR="/opt/grc-app"
read -rp "Directorio de instalación [${INSTALL_DIR}]:         " INPUT_DIR
INSTALL_DIR="${INPUT_DIR:-$INSTALL_DIR}"

SERVICE_USER="grcapp"
PORT=8000

info "Empresa    : $EMPRESA"
info "Timezone   : UTC${TZ_OFFSET}"
info "Directorio : $INSTALL_DIR"
info "Puerto     : $PORT (Gunicorn, interno)"
echo ""
read -rp "¿Continuar? [S/n]: " CONFIRM
[[ "${CONFIRM,,}" == "n" ]] && echo "Instalación cancelada." && exit 0

# ── Dependencias del sistema ─────────────────────────────────────────────────
section "Dependencias del sistema"

apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv python3-dev \
    build-essential libssl-dev nginx curl > /dev/null
ok "Paquetes del sistema instalados."

# Verificar Python >= 3.10
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_OK=$(python3 -c "import sys; print(int(sys.version_info >= (3,10)))")
[[ "$PY_OK" != "1" ]] && error "Se requiere Python 3.10+. Versión detectada: $PY_VER"
ok "Python $PY_VER detectado."

# ── Usuario del sistema ──────────────────────────────────────────────────────
section "Usuario del servicio"

if id "$SERVICE_USER" &>/dev/null; then
    warn "Usuario '$SERVICE_USER' ya existe, se reutiliza."
else
    useradd -r -s /usr/sbin/nologin "$SERVICE_USER"
    ok "Usuario '$SERVICE_USER' creado."
fi

# ── Directorio de instalación ────────────────────────────────────────────────
section "Archivos de la aplicación"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/logs" "$INSTALL_DIR/instance"

# Copiar fuentes (excluyendo .venv, instance, __pycache__, archivos dev)
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

ok "Archivos copiados a $INSTALL_DIR."

# ── Entorno virtual ──────────────────────────────────────────────────────────
section "Entorno virtual Python"

python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install --quiet \
    -r "$INSTALL_DIR/requirements.txt" \
    -r "$INSTALL_DIR/requirements-prod.txt"
ok "Dependencias instaladas."

# ── Archivo .env ─────────────────────────────────────────────────────────────
section "Configuración del entorno (.env)"

SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

cat > "$INSTALL_DIR/.env" <<EOF
# Generado por install.sh el $(date '+%Y-%m-%d %H:%M')
# NO compartir este archivo — contiene la clave secreta de sesión.

SECRET_KEY=${SECRET_KEY}
APP_NAME=Licencias & GRC · ${EMPRESA}
APP_TIMEZONE_OFFSET_HOURS=${TZ_OFFSET}

# Base de datos (SQLite por defecto — no modificar salvo casos especiales)
# DATABASE_URL=sqlite:///instance/grc_app.db
EOF

chmod 600 "$INSTALL_DIR/.env"
ok "Archivo .env generado."

# ── Base de datos e inicialización ───────────────────────────────────────────
section "Base de datos"

cd "$INSTALL_DIR"
"$INSTALL_DIR/.venv/bin/python" -c "
from app import create_app
from app.extensions import db
app = create_app()
with app.app_context():
    db.create_all()
print('Tablas creadas.')
"

"$INSTALL_DIR/.venv/bin/python" seed.py
ok "Base de datos inicializada. Usuario admin creado (admin / admin123)."
warn "Cambiar la contraseña del usuario admin antes de continuar."

# ── Permisos ─────────────────────────────────────────────────────────────────
section "Permisos"

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
chmod -R 750 "$INSTALL_DIR"
chmod 600 "$INSTALL_DIR/.env"
ok "Permisos configurados."

# ── Servicio systemd ─────────────────────────────────────────────────────────
section "Servicio systemd"

SERVICE_NAME="grc-app"

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Licencias & GRC — ${EMPRESA}
After=network.target

[Service]
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/.venv/bin/gunicorn \\
    --workers 3 \\
    --bind 127.0.0.1:${PORT} \\
    --access-logfile ${INSTALL_DIR}/logs/access.log \\
    --error-logfile ${INSTALL_DIR}/logs/error.log \\
    run:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
systemctl start "${SERVICE_NAME}.service"

sleep 2
if systemctl is-active --quiet "${SERVICE_NAME}.service"; then
    ok "Servicio '${SERVICE_NAME}' activo y habilitado."
else
    warn "El servicio no arrancó correctamente. Revisar: journalctl -u ${SERVICE_NAME} -n 50"
fi

# ── Nginx ────────────────────────────────────────────────────────────────────
section "Nginx (proxy inverso)"

read -rp "¿Configurar Nginx ahora? [S/n]: " CONF_NGINX
if [[ "${CONF_NGINX,,}" != "n" ]]; then
    read -rp "Nombre de dominio o IP del servidor: " SERVER_NAME
    SERVER_NAME="${SERVER_NAME:-_}"

    cat > "/etc/nginx/sites-available/${SERVICE_NAME}" <<EOF
server {
    listen 80;
    server_name ${SERVER_NAME};

    location /static/ {
        alias ${INSTALL_DIR}/static/;
        expires 7d;
        add_header Cache-Control "public";
    }

    location / {
        proxy_pass         http://127.0.0.1:${PORT};
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        client_max_body_size 50M;
    }
}
EOF

    ln -sf "/etc/nginx/sites-available/${SERVICE_NAME}" \
           "/etc/nginx/sites-enabled/${SERVICE_NAME}"
    nginx -t && systemctl reload nginx
    ok "Nginx configurado para ${SERVER_NAME}."
    warn "Para HTTPS, instalar Certbot: certbot --nginx -d ${SERVER_NAME}"
fi

# ── Resumen final ────────────────────────────────────────────────────────────
section "Instalación completada"

echo ""
echo -e "  ${GREEN}${BOLD}✓ Licencias & GRC instalado correctamente${NC}"
echo ""
echo -e "  Empresa       : ${BOLD}${EMPRESA}${NC}"
echo -e "  Directorio    : ${BOLD}${INSTALL_DIR}${NC}"
echo -e "  Servicio      : ${BOLD}${SERVICE_NAME}${NC}"
echo -e "  Puerto interno: ${BOLD}${PORT}${NC}"
echo ""
echo -e "  ${YELLOW}Próximos pasos:${NC}"
echo "  1. Cambiar contraseña del usuario admin en la interfaz web"
echo "  2. Importar datos SAP desde el módulo de Importación"
echo "  3. (Opcional) Configurar HTTPS con Certbot"
echo ""
echo -e "  Comandos útiles:"
echo "    sudo systemctl status ${SERVICE_NAME}"
echo "    sudo journalctl -u ${SERVICE_NAME} -f"
echo "    sudo tail -f ${INSTALL_DIR}/logs/error.log"
echo ""
