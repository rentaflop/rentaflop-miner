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


class Config(object):
    SQLALCHEMY_DATABASE_URI = "mysql+pymysql://root:daemon@localhost/daemon"
    SECRET_KEY = uuid.uuid4().hex
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 500 * 1024 * 1024


def get_app_db():
    """
    returns app and db objects and does necessary setup
    """
    app = Flask(__name__)
    app.config.from_object(Config)
    db = SQLAlchemy(app)

    return app, db
