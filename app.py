"""
PedalPower - Smart Pedal-Powered Electricity Generation System
--------------------------------------------------------------
STARTUP INSTRUCTIONS:
1. pip install flask flask-cors flask-sqlalchemy flask-login werkzeug requests pyserial
2. python setup_vendor.py
3. python app.py
"""

from flask import Flask
from flask_cors import CORS
from flask_login import LoginManager
from config import Config
from models.models import db, User
from routes.auth import auth as auth_blueprint
from routes.main import main as main_blueprint
from routes.api import api as api_blueprint
from esp32_serial_bridge import start_esp32_serial_bridge
import sqlalchemy as sa
import os

def run_migrations(app):
    """Simple migration helper to add missing columns to existing database."""
    with app.app_context():
        engine = db.engine
        inspector = sa.inspect(engine)
        
        # Check PedalSession table
        columns = [c['name'] for c in inspector.get_columns('pedal_session')]
        
        with engine.connect() as conn:
            if 'raw_rpm' not in columns:
                conn.execute(sa.text('ALTER TABLE pedal_session ADD COLUMN raw_rpm FLOAT DEFAULT 0.0'))
            if 'raw_voltage' not in columns:
                conn.execute(sa.text('ALTER TABLE pedal_session ADD COLUMN raw_voltage FLOAT DEFAULT 0.0'))
            if 'raw_current' not in columns:
                conn.execute(sa.text('ALTER TABLE pedal_session ADD COLUMN raw_current FLOAT DEFAULT 0.0'))
            if 'power_w' not in columns:
                conn.execute(sa.text('ALTER TABLE pedal_session ADD COLUMN power_w FLOAT DEFAULT 0.0'))
            conn.commit()

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Initialize extensions
    db.init_app(app)
    CORS(app)
    
    login_manager = LoginManager()
    login_manager.login_view = 'auth.login'
    login_manager.login_message_category = 'warning'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Register blueprints
    app.register_blueprint(auth_blueprint)
    app.register_blueprint(main_blueprint)
    app.register_blueprint(api_blueprint)

    # Run migrations before create_all
    run_migrations(app)

    # Create database tables
    with app.app_context():
        db.create_all()

    # Optional: start ESP32 serial bridge (set ESP32_SERIAL_PORT=COMx)
    # Flask debug reloader can spawn multiple processes; start bridge:
    # - always when reloader is disabled
    # - only in the reloader "main" process when reloader is enabled
    use_reloader = (os.environ.get("PEDALPOWER_USE_RELOADER") or "1") == "1"
    if (not use_reloader) or (os.environ.get("WERKZEUG_RUN_MAIN") == "true"):
        start_esp32_serial_bridge(app)

    return app

if __name__ == '__main__':
    # Decide whether to run the Flask reloader BEFORE creating the app.
    esp32_enabled = (os.environ.get("ESP32_SERIAL_ENABLED") or "1").strip().lower() not in {"0", "false", "no"}
    esp32_port_set = bool((os.environ.get("ESP32_SERIAL_PORT") or "").strip())
    use_reloader = not (esp32_enabled and esp32_port_set)
    os.environ["PEDALPOWER_USE_RELOADER"] = "1" if use_reloader else "0"

    app = create_app()
    print("-----------------------------------------------")
    print("PedalPower Server Started")
    print("Dashboard: http://127.0.0.1:5000")
    print("-----------------------------------------------")
    # When reading ESP32 over Serial, Flask's debug reloader can spawn multiple processes and
    # cause COM-port lock/PermissionError. Disable the reloader when ESP32 is enabled.
    if not use_reloader and app.debug:
        print("[ESP32] Flask reloader disabled to prevent COM port lock.")

    app.run(debug=True, port=5000, use_reloader=use_reloader)
