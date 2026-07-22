# Ejecutar una sola vez, parado dentro de la carpeta webapp/, para dejar
# el proyecto versionado con git y arrancar la rama de la version 1.1.

# 1) Borra el intento de git que quedo a medias (creado por Claude desde
#    un entorno sin permiso para tocar archivos .lock de git).
if (Test-Path .git) {
    Remove-Item -Recurse -Force .git
}

# 2) Inicializa el repositorio
git init
git config user.name "Cris"
git config user.email "cristian.perezdm@gmail.com"

# 3) Primer commit: estado actual = version 1.0
git add -A
git commit -m "v1.0: import por carpeta, indicador de progreso, paquete de deploy"
git branch -M main
git tag v1.0

# 4) Rama nueva para seguir trabajando en la version siguiente
git checkout -b v1.1-dev

Write-Host ""
Write-Host "Listo. Rama activa:"
git branch
