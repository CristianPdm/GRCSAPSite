# Guía de instalación en servidor Linux — v1.0

Pensada para una instalación nueva en un servidor Linux (Ubuntu/Debian o
similar) con acceso `sudo`. Cada paso incluye una breve explicación de
qué hace y por qué, no solo el comando.

## 0. Qué incluye el paquete

```
grc-simpa/
  app/              -> código de la aplicación (Flask)
  templates/         -> páginas HTML
  static/             -> CSS, JS, imágenes
  deploy/             -> esta guía + archivos de systemd y Nginx
  config.py, run.py, seed.py
  requirements.txt        -> dependencias de la app (multiplataforma)
  requirements-prod.txt   -> gunicorn (solo Linux/producción)
  .env.example             -> plantilla de variables de entorno
```

No incluye base de datos (`instance/grc_simpa.db`): se crea vacía en el
servidor con `seed.py` (paso 5), para no llevar datos de prueba a
producción.

## 1. Requisitos previos

- Python 3.10 o superior (`python3 --version`).
- Paquete `python3-venv` (entornos virtuales) y `python3-pip`.
- Opcional pero recomendado: Nginx (para HTTPS y para no exponer
  Gunicorn directo a internet) y Certbot (certificado SSL gratuito).

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx
```

## 2. Copiar el paquete al servidor

Elegimos `/opt/grc-simpa` como ruta de instalación (puede ser otra; si
se cambia, hay que ajustarla también en `deploy/grc-simpa.service` y en
`deploy/grc-simpa.nginx.conf`).

```bash
sudo mkdir -p /opt/grc-simpa
# Subir el .tar.gz al servidor (scp, sftp, etc.) y luego:
sudo tar -xzf grc-simpa-v1.0.tar.gz -C /opt/grc-simpa --strip-components=1
```

## 3. Usuario dedicado (no usar root)

Por seguridad, el sitio corre con un usuario propio sin privilegios, no
como `root`.

```bash
sudo useradd -r -s /usr/sbin/nologin grcsimpa
sudo mkdir -p /opt/grc-simpa/logs /opt/grc-simpa/instance
sudo chown -R grcsimpa:grcsimpa /opt/grc-simpa
```

## 4. Entorno virtual y dependencias

Un entorno virtual (`.venv`) aísla las librerías de Python de esta app
del resto del sistema, para no pisar versiones usadas por otros
programas del servidor.

```bash
cd /opt/grc-simpa
sudo -u grcsimpa python3 -m venv .venv
sudo -u grcsimpa .venv/bin/pip install -r requirements.txt -r requirements-prod.txt
```

## 5. Variables de entorno (clave secreta)

`SECRET_KEY` firma las cookies de sesión de los usuarios; sin una clave
propia, cualquiera podría falsificar una sesión. Generar una y guardarla
en un archivo `.env` que **no se comparte ni se versiona**:

```bash
cd /opt/grc-simpa
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"
# copiar el resultado como valor de SECRET_KEY dentro de .env
sudo chown grcsimpa:grcsimpa .env
sudo chmod 600 .env
```

## 6. Crear la base de datos y el primer Administrador

```bash
sudo -u grcsimpa /opt/grc-simpa/.venv/bin/python seed.py
```

Pide usuario, nombre completo y contraseña del primer Administrador.
Solo hace falta correrlo la primera vez (si ya hay usuarios, no crea
ninguno nuevo, así que es seguro volver a ejecutarlo después por error).

## 7. Servicio systemd (arranque automático)

`deploy/grc-simpa.service` ya viene armado para correr con Gunicorn
(servidor WSGI apto para producción; `run.py` con `flask run` es solo
para desarrollo). Revisar que las rutas dentro del archivo coincidan con
`/opt/grc-simpa` antes de copiarlo:

```bash
sudo cp deploy/grc-simpa.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now grc-simpa
sudo systemctl status grc-simpa
```

`enable --now` hace que arranque solo en cada reinicio del servidor, y
lo arranca ya mismo. Si `status` no muestra "active (running)", revisar
`/opt/grc-simpa/logs/error.log`.

## 8. Nginx como entrada pública

Gunicorn queda escuchando solo en `127.0.0.1:8000` (no expuesto a la
red); Nginx es el que recibe las conexiones externas en el puerto 80/443
y se las pasa. Esto permite además servir los archivos estáticos más
rápido y, más adelante, agregar HTTPS sin tocar la app.

```bash
sudo cp deploy/grc-simpa.nginx.conf /etc/nginx/sites-available/grc-simpa
sudo ln -s /etc/nginx/sites-available/grc-simpa /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Ajustar `server_name` en ese archivo por el dominio o IP real antes de
recargar. Para HTTPS con certificado gratuito (recomendado si se accede
desde fuera de la red interna):

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d grc-simpa.simpa.local
```

## 9. Verificación

- `http://<servidor>/` debe mostrar la pantalla de login.
- Entrar con el usuario Administrador creado en el paso 6.
- Probar una importación de Excel chica para confirmar que
  `instance/` (donde vive la base SQLite) tiene permisos de escritura
  para el usuario `grcsimpa`.

## 10. Backups

La base de datos es un solo archivo SQLite: `instance/grc_simpa.db`.
Alcanza con copiarlo periódicamente (parado o no, SQLite tolera copiarlo
en caliente para backup, aunque lo más prolijo es un cron nocturno):

```bash
0 2 * * * cp /opt/grc-simpa/instance/grc_simpa.db /opt/grc-simpa/backups/grc_simpa_$(date +%Y%m%d).db
```

(Crear antes la carpeta `backups/` con permisos del usuario `grcsimpa`,
y definir aparte una rotación/limpieza de copias viejas según el espacio
disponible.)

## 11. Actualizar a una versión futura

Cuando se entregue un paquete v1.1, v1.2, etc.:

```bash
sudo systemctl stop grc-simpa
# reemplazar app/, templates/, static/, run.py, config.py, seed.py,
# requirements*.txt por los nuevos — SIN tocar instance/ ni .env
sudo -u grcsimpa /opt/grc-simpa/.venv/bin/pip install -r requirements.txt -r requirements-prod.txt
sudo -u grcsimpa /opt/grc-simpa/.venv/bin/python seed.py   # solo crea lo que falte, no borra nada
sudo systemctl start grc-simpa
```

Si la nueva versión agrega una columna a una tabla existente (no solo
tablas nuevas), el instructivo de esa versión va a indicar un script de
migración aparte — `seed.py` por sí solo no altera columnas de tablas ya
creadas.
