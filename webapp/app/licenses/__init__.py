from flask import Blueprint

bp = Blueprint("licenses", __name__)

from app.licenses import routes  # noqa: E402,F401
