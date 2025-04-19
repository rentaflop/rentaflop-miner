"""
contains shared global variables and configurations
"""
import os
import logging
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
import uuid


def _get_logger(log_file):
    """
    modules use this to create/retrieve and configure how logging works for their specific module
    """
    module_logger = logging.getLogger("daemon.log")
    handler = logging.FileHandler(log_file)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(filename)s:%(lineno)d %(levelname)s %(asctime)s - %(message)s"))
    module_logger.addHandler(handler)
    module_logger.setLevel(logging.DEBUG)

    return module_logger


LOG_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "daemon.log")
REGISTRATION_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "rentaflop_config.json")
CACHE_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "render_file_cache")
FIRST_STARTUP = not os.path.exists(LOG_FILE)
DAEMON_LOGGER = _get_logger(LOG_FILE)
# find good open ports at https://stackoverflow.com/questions/10476987/best-tcp-port-number-range-for-internal-applications
DAEMON_PORT = 46443


class Config(object):
    SQLALCHEMY_DATABASE_URI = "mysql+pymysql://root:daemon@localhost/daemon"
    SECRET_KEY = uuid.uuid4().hex
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 500 * 1024 * 1024

app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)

class Overclock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # looks like '{"oc_settings": ..., "oc_hash": ...}'
    oc_settings = db.Column(db.String(2048))

    def __repr__(self):
        return f"<Overclock {self.oc_settings}>"

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer)
    task_dir = db.Column(db.String(128))
    main_file_path = db.Column(db.String(1024))
    start_frame = db.Column(db.Integer)
    end_frame = db.Column(db.Integer)
    uuid_str = db.Column(db.String(128))
    blender_version = db.Column(db.String(128))
    is_cpu = db.Column(db.Boolean)
    cuda_visible_devices = db.Column(db.String(64))
    is_price_calculation = db.Column(db.Boolean)

    def __repr__(self):
        return f"<Task {self.task_id} {self.task_dir}>"
