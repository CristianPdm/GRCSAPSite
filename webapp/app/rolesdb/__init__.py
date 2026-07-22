from flask import Blueprint

bp = Blueprint("rolesdb", __name__, url_prefix="/roles")
from app.rolesdb import routes  # noqa: E402,F401
