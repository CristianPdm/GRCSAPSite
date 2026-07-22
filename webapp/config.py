import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    """Configuracion base de la aplicacion."""

    # Clave usada para firmar la sesion. En produccion debe definirse
    # con la variable de entorno SECRET_KEY y no dejarse el valor por defecto.
    SECRET_KEY = os.environ.get("SECRET_KEY", "cambia-esta-clave-en-produccion")

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "instance", "grc_simpa.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Nombre mostrado en la barra de navegacion. Se configura por cliente
    # via variable de entorno APP_NAME en el archivo .env del servidor.
    APP_NAME = os.environ.get("APP_NAME", "Licencias & GRC")
    APP_VERSION = "2.2"


    # Offset horario respecto a UTC, usado solo para MOSTRAR fechas/horas
    # que se guardan en UTC (datetime.utcnow()). Argentina/Uruguay = -3.
    # Se usa offset fijo (no zoneinfo) para no depender de tzdata del S.O.
    APP_TIMEZONE_OFFSET_HOURS = int(os.environ.get("APP_TIMEZONE_OFFSET_HOURS", "-3"))
