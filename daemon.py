"""
host daemon that communicates with rentaflop's servers for instructions
functions include, but are not limited to, software updates, system updates,
guest and crypto mining session initiation/termination, uninstallation
usage:
    python daemon.py
"""
import os
import logging
import uuid
from flask import Flask, jsonify, request

    
def _get_logger():
    """
    modules use this to create/retrieve and configure how logging works for their specific module
    """
    module_logger = logging.getLogger(name)
    handler = logging.FileHandler(LOG_FILE)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(filename)s:%(lineno)d %(levelname)s %(asctime)s - %(message)s"))
    module_logger.addHandler(handler)
    module_logger.setLevel(logging.DEBUG)

    return module_logger


DAEMON_LOGGER = _get_logger()
LOG_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "daemon.log")
CMD_TO_FUNC = {
    "mine": mine,
    "update": update,
    "uninstall": uninstall,
    "send_logs": send_logs,
}


def _handle_startup():
    """
    checks to see if there's an existing log file to handle startup scenarios
    if no log file, then assume first startup
    if log file exists, check last command to see if it was an update
    if not update, assume crash and error state
    if update, log update completed
    """
    # ensure daemon flask server is accessible
    internal_ip = _run_shell_cmd("hostname -I | awk '{print $1}'")
    _run_shell_cmd("upnpc -a {internal_ip} 44443 44443 tcp")
    
    log_file_exists = os.path.exists(LOG_FILE)
    daemon_py = os.path.realpath(__file__)
    if not log_file_exists:
        # ensure daemon is run on system startup
        _run_shell_cmd(f'(crontab -u root -l; echo "@reboot python3 {daemon_py}") | crontab -u root -')

        return

    # get last line of log file
    with open(LOG_FILE, 'rb') as f:
        # catch OSError in case of a one line file
        try:
            f.seek(-2, os.SEEK_END)
            while f.read(1) != b'\n':
                f.seek(-2, os.SEEK_CUR)
        except OSError:
            f.seek(0)
        last_line = f.readline().decode()

    is_update = ("sudo reboot" in last_line) or ("python3 daemon.py" in last_line)
    if not is_update:
        # TODO error status
        return

    DAEMON_LOGGER.debug(f"Exiting update.")


def _log_before_after(func, params):
    """
    wrapper to log debug info before and after each daemon command
    """
    def wrapper():
        DAEMON_LOGGER.debug(f"Entering {func.__name__} with params {params}...")
        ret_val = func(params)
        DAEMON_LOGGER.debug(f"Exiting {func.__name__}.")

        return ret_val

    return wrapper


def _run_shell_cmd(cmd, quiet=False):
    """
    run cmd and log output
    """
    if not quiet:
        DAEMON_LOGGER.debug(f'''Running command {cmd}...''')
    output = subprocess.check_output(cmd, shell=True, text=True)
    if output and not quiet:
        DAEMON_LOGGER.debug(f'''Output for {cmd}: {output}''')

    return output


def mine(params):
    """
    handle commands related to mining, whether crypto mining or guest "mining"
    params looks like {"type": "crypto" | "gpc"}
    """
    mine_type = params["type"]
    if mine_type == "crypto":
        # TODO launch crypto docker
        return
    elif mine_type == "gpc":
        # TODO launch guest sandbox
        return


def update(params):
    """
    handle commands related to rentaflop software and system updates
    params looks like {"type": "rentaflop" | "system"}
    """
    update_type = params["type"]
    if update_type == "rentaflop":
        _run_shell_cmd("git pull")
        # daemon will shut down (but not full system) so this ensures it starts back up again
        _run_shell_cmd('echo "sleep 3; python3 daemon.py" | at now')

        return True
    elif update_type == "system":
        _run_shell_cmd("sudo apt-get update -y")
        _run_shell_cmd("DEBIAN_FRONTEND=noninteractive \
        sudo apt-get \
        -o Dpkg::Options::=--force-confold \
        -o Dpkg::Options::=--force-confdef \
        -y --allow-downgrades --allow-remove-essential --allow-change-held-packages \
        dist-upgrade")
        _run_shell_cmd("sudo reboot")
        

def uninstall(params):
    """
    uninstall rentaflop from this machine
    """
    # stop and remove all rentaflop docker containers and images
    # TODO rename images/containers
    _run_shell_cmd('docker stop $(docker ps --filter "name=ssh*" -q)')
    _run_shell_cmd('docker rmi $(docker images -q "dasokol/*") $(docker images "nvidia/cuda" -a -q)')
    # send logs first
    send_logs(params)
    # clean up rentaflop host software
    _run_shell_cmd("upnpc -d 44443 tcp")
    daemon_py = os.path.realpath(__file__)
    _run_shell_cmd(f"crontab -u root -l | grep -v 'python3 {daemon_py}' | crontab -u root -"
    _run_shell_cmd("rm -rf ../rentaflop-host", True)

    return True


def send_logs(params):
    """
    gather host logs and send back to rentaflop servers
    """
    with open(LOG_FILE, "r") as f:
        logs = f.read()

    # TODO send logs to server


@app.route("/", methods=["POST"])
def index():
    request_json = request.get_json()
    cmd = request_json.get("cmd")
    params = request_json.get("params")
    func = CMD_TO_FUNC.get(cmd)
    finished = False
    if func:
        try:
            finished = _log_before_after(func, params)
        except Exception as e:
            DAEMON_LOGGER.exception(f"Caught exception: {e}")
    if finished:
        shutdown_func = request.environ.get('werkzeug.server.shutdown')
        if shutdown_func is None:
            raise RuntimeError('Not running with the Werkzeug Server!')
        shutdown_func()

    return jsonify("200")


def main():
    _handle_startup()
    app = Flask(__name__)
    app.secret_key = uuid.uuid4().hex
    app.run(host='0.0.0.0', port=44443)


if __name__=="__main__":
    main()
