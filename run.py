from app import create_app
from config.settings import FLASK_DEBUG, FLASK_PORT, FLASK_RELOAD

if __name__ == "__main__":
    app = create_app()
    app.run(debug=FLASK_DEBUG, use_reloader=FLASK_RELOAD, port=FLASK_PORT)
