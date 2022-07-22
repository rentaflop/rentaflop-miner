"""
contains shared global variables and configurations
"""
import os
import logging
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
import pymysql
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
FIRST_STARTUP = not os.path.exists(LOG_FILE)
DAEMON_LOGGER = _get_logger(LOG_FILE)

app = Flask(__name__)
os.system("/etc/init.d/mysql start")
os.system('''mysql -u root -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY 'daemon';"''')
os.system('mysql -u root -pdaemon -e "create database daemon;"')

class Config(object):
    SQLALCHEMY_DATABASE_URI = "mysql+pymysql://root:daemon@localhost/daemon"
    SECRET_KEY = uuid.uuid4().hex
    SQLALCHEMY_TRACK_MODIFICATIONS = False

app.config.from_object(Config)
db = SQLAlchemy(app)

class Overclock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # looks like '{"oc_settings": ..., "oc_hash": ...}'
    oc_settings = db.Column(db.String(2048))

    def __repr__(self):
        return f"<Overclock {self.oc_settings}>"

db.drop_all(app=app)
db.create_all(app=app)
