from flask import Flask
from app.db import init_db


def create_app() -> Flask:
    app = Flask(__name__)

    try:
        init_db()
    except Exception as exc:
        app.logger.warning("Database init skipped: %s", exc)

    from app.routes import bp
    app.register_blueprint(bp)

    return app
