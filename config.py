"""
contains shared global variables and configurations
"""
import os
import logging
import requests


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


def _get_registration():
    """
    return registration details from registration file or register if it doesn't exist
    """
    is_registered = os.path.exists(REGISTRATION_FILE)
    rentaflop_id = None
    if not is_registered:
        # register host with rentaflop
        try:
            response = requests.post(DAEMON_URL, data={})
            rentaflop_id = response.json()["rentaflop_id"]
        except:
            # TODO retry hourly on error state? log to rentaflop endpoint?
            DAEMON_LOGGER.error("Failed registration! Exiting...")
            raise
        with open(REGISTRATION_FILE, "w") as f:
            f.write(rentaflop_id)
    else:
        with open(REGISTRATION_FILE, "r") as f:
            rentaflop_id = f.read().strip()

    return rentaflop_id


LOG_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "daemon.log")
REGISTRATION_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "registration.txt")
FIRST_STARTUP = (not os.path.exists(LOG_FILE))
DAEMON_LOGGER = _get_logger(LOG_FILE)
DAEMON_URL = "https://portal.rentaflop.com/api/host/daemon"
RENTAFLOP_ID = _get_registration()
