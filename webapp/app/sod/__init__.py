from flask import Blueprint

bp = Blueprint("sod", __name__)

from app.sod import routes  # noqa: E402,F401
