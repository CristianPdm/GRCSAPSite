from flask import Blueprint

bp = Blueprint("chat", __name__)

from app.chat import routes  # noqa: E402, F401
