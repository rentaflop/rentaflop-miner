"""
host daemon that communicates with rentaflop's servers for instructions
functions include, but are not limited to, software updates, system updates,
guest and crypto mining session initiation/termination, uninstallation
usage:
    python daemon.py
    # used to indicate rentaflop code update still in progress so we must call
    # update function again; not used during system updates or on second update
    python daemon.py update
"""
import os
import logging
import uuid
import multiprocessing
from flask import Flask, jsonify, request, abort, redirect
from config import DAEMON_LOGGER, FIRST_STARTUP, LOG_FILE
from utils import *
import sys
import requests
import time


app = Flask(__name__)


def _get_registration():
    """
    return registration details from registration file or register if it doesn't exist
    """
    registration_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), "registration.txt")
    is_registered = os.path.exists(registration_file)
    daemon_url = "https://portal.rentaflop.com/api/host/daemon"
    rentaflop_id = None
    if not is_registered:
        # register host with rentaflop
        try:
            ip = run_shell_cmd('upnpc -s | grep ExternalIPAddress | cut -d " " -f 3', format_output=False).replace("\n", "")
            data = {"state": get_state(), "ip": ip}
            response = requests.post(daemon_url, data=data)
            rentaflop_id = response.json()["rentaflop_id"]
        except:
            # TODO retry hourly on error state? log to rentaflop endpoint?
            DAEMON_LOGGER.error("Failed registration! Exiting...")
            raise
        with open(registration_file, "w") as f:
            f.write(rentaflop_id)
    else:
        with open(registration_file, "r") as f:
            rentaflop_id = f.read().strip()

    return rentaflop_id


def _enable_restart_on_boot():
    """
    places restart in crontab if not already present
    ensures daemon is run on system startup
    """
    daemon_py = os.path.realpath(__file__)
    # first remove crontab entry if it exists
    run_shell_cmd(f"crontab -u root -l | grep -v 'python3 {daemon_py}' | crontab -u root -")
    run_shell_cmd(f'(crontab -u root -l; echo "@reboot python3 {daemon_py}") | crontab -u root -')

    
def _first_startup():
    """
    run rentaflop installation steps
    """
    # list of sources for security updates
    run_shell_cmd("sudo sh -c 'grep ^deb /etc/apt/sources.list | grep security > /etc/apt/sources.security.only.list'")
    # perform system update
    update({"type": "system"}, reboot=False)
    # install dependencies
    run_shell_cmd("sudo apt-get install ca-certificates curl gnupg lsb-release -y")
    run_shell_cmd("curl -fsSL https://download.docker.com/linux/debian/gpg \
    | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg --batch --yes")
    run_shell_cmd('echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] \
    https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null')
    run_shell_cmd("distribution=$(. /etc/os-release; echo $ID$VERSION_ID) \
    && curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add - \
    && curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | sudo tee /etc/apt/sources.list.d/nvidia-docker.list")
    run_shell_cmd("sudo apt-get update -y")
    run_shell_cmd("sudo apt-get install miniupnpc docker-ce docker-ce-cli containerd.io nvidia-docker2 -y")
    # docker setup
    run_shell_cmd("sudo sed -i 's/#no-cgroups = false/no-cgroups = true/' /etc/nvidia-container-runtime/config.toml")
    run_shell_cmd(r'''sudo sed -i '$s/}/,\n"userns-remap":"default"}/' /etc/docker/daemon.json''')
    run_shell_cmd("sudo systemctl restart docker")
    run_shell_cmd("sudo docker build -f Dockerfile -t rentaflop/sandbox .")
    _enable_restart_on_boot()
    run_shell_cmd("sudo reboot")


def _subsequent_startup():
    """
    handle case where log file already exists and we've had a prior daemon startup
    """
    # if update passed as clarg, then we need to call update again to handle situation when
    # update function itself has been updated in the rentaflop code update
    if len(sys.argv) > 1 and sys.argv[1] == "update":
        DAEMON_LOGGER.debug("Entering second update...")
        update({"type": "rentaflop"}, second_update=True)
        DAEMON_LOGGER.debug("Exiting second update.")
        # flushing logs and exiting daemon now since it's set to restart in 3 seconds
        logging.shutdown()
        sys.exit(0)

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

    is_update = ("sudo reboot" in last_line) or ("python3 daemon.py" in last_line) or \
        ("Exiting second update." in last_line)
    if is_update:
        DAEMON_LOGGER.debug("Exiting update.")
    else:
        # error state
        DAEMON_LOGGER.debug("Daemon crashed.")

    # TODO better way to do this? sleeping so we write to cron only after hiveos does and so nvidia-uvm appears
    time.sleep(10)
    _enable_restart_on_boot()


def _handle_startup():
    """
    uses log file existence to handle startup scenarios
    if no log file, then assume first startup
    if first startup, run rentaflop installation steps
    if log file exists, check last command to see if it was an update
    if not update, assume crash and error state
    if update, log update completed
    """
    # NOTE: this if else must be first as we need to immediately check last line in log file during updates
    if FIRST_STARTUP:
        _first_startup()
    else:
        _subsequent_startup()

    DAEMON_LOGGER.debug("Starting daemon...")
    run_shell_cmd("sudo nvidia-smi -pm 1", quiet=True)
    # set IGD to speed up upnpc commands
    global IGD
    global RENTAFLOP_ID
    IGD = get_igd()
    RENTAFLOP_ID = _get_registration()
    # ensure daemon flask server is accessible
    # HTTPS port
    run_shell_cmd(f"upnpc -u {IGD} -e 'rentaflop' -r 46443 tcp")
    _, gpu_indexes = get_gpus()
    for gpu_index in gpu_indexes:
        mine({"type": "crypto", "action": "start", "gpu": gpu_index})


def mine(params):
    """
    handle commands related to mining, whether crypto mining or guest "mining"
    params looks like {"type": "crypto" | "gpc", "action": "start" | "stop", "gpu": "0"}
    """
    mine_type = params["type"]
    action = params["action"]
    gpu = int(params["gpu"])
    container_name = f"rentaflop-sandbox-{gpu}-{mine_type}"
    # SSH port
    port = 46422 + gpu

    if action == "start":
        # TODO '--gpus all' problematic to use? it's supposed to pass all gpus but only specified device is available, but can't seem to get it to work without 'all'
        # TODO set constraints on ram, cpu, bandwidth https://docs.docker.com/engine/reference/run/
        run_shell_cmd(f"sudo docker run --gpus all --device /dev/nvidia{gpu}:/dev/nvidia0 --device /dev/nvidiactl:/dev/nvidiactl \
        --device /dev/nvidia-modeset:/dev/nvidia-modeset --device /dev/nvidia-uvm:/dev/nvidia-uvm --device /dev/nvidia-uvm-tools:/dev/nvidia-uvm-tools \
        -p {port}:22 --rm --name {container_name} --env RENTAFLOP_SANDBOX_TYPE={mine_type} --env RENTAFLOP_ID={RENTAFLOP_ID} -dt rentaflop/sandbox")
        # crypto doesn't expose ports externally while gpc does
        if mine_type == "gpc":
            # find good open ports at https://stackoverflow.com/questions/10476987/best-tcp-port-number-range-for-internal-applications
            run_shell_cmd(f"upnpc -u {IGD} -e 'rentaflop' -r {port} tcp")
    elif action == "stop":
        run_shell_cmd(f"docker kill {container_name}")
        # does nothing if port is not open
        run_shell_cmd(f"upnpc -u {IGD} -d {port} tcp")


def _stop_all():
    """
    stop all rentaflop docker containers
    """
    containers = run_shell_cmd('docker ps --filter "name=rentaflop*" -q', format_output=False).replace("\n", " ")
    if containers:
        run_shell_cmd(f'docker stop {containers}')

    
def update(params, reboot=True, second_update=False):
    """
    handle commands related to rentaflop software and system updates
    params looks like {"type": "rentaflop" | "system"}
    reboot controls whether system update will reboot
    second_update is set to True to indicate current update code running is already up to date,
    False if it hasn't been updated yet
    if second_update is True, we're performing the real update, as latest code for this function
    is currently running, whereas on the first update this function may not have been up to date
    """
    update_type = params["type"]
    if update_type == "rentaflop":
        # must run all commands even if second update
        run_shell_cmd("git pull")
        # stop all rentaflop docker containers
        _stop_all()
        run_shell_cmd("sudo docker build -f Dockerfile -t rentaflop/sandbox .")
        update_param = "" if second_update else " update"
        # daemon will shut down (but not full system) so this ensures it starts back up again
        run_shell_cmd(f'echo "sleep 3; python3 daemon.py{update_param}" | at now')

        return True
    elif update_type == "system":
        run_shell_cmd("sudo apt-get update -y")
        # perform only security updates
        run_shell_cmd(r'''DEBIAN_FRONTEND=noninteractive \
        sudo apt-get -s dist-upgrade -y -o Dir::Etc::SourceList=/etc/apt/sources.security.only.list \
        -o Dir::Etc::SourceParts=/dev/null  | grep "^Inst" | awk -F " " {'print $2'}''')
        if reboot:
            run_shell_cmd("sudo reboot")
        

def uninstall(params):
    """
    uninstall rentaflop from this machine
    """
    # stop and remove all rentaflop docker containers and images
    _stop_all()
    run_shell_cmd('docker rmi $(docker images -a -q "rentaflop/sandbox") $(docker images | grep none | awk "{ print $3; }") $(docker images "nvidia/cuda" -a -q)')
    # send logs first; do we need this?
    # send_logs(params)
    # clean up rentaflop host software
    run_shell_cmd(f"upnpc -u {IGD} -d 46443 tcp")
    daemon_py = os.path.realpath(__file__)
    run_shell_cmd(f"crontab -u root -l | grep -v 'python3 {daemon_py}' | crontab -u root -")
    run_shell_cmd("rm -rf ../rentaflop-host", quiet=True)

    return True


def send_logs(params):
    """
    gather host logs and send back to rentaflop servers
    """
    with open(LOG_FILE, "r") as f:
        logs = f.readlines()
        # remove trailing newlines and empty lines
        logs = [log[:-1] for log in logs if not log.isspace()]

    return {"logs": logs}


def status(params):
    """
    return the state of this host
    """
    return {"state": get_state(IGD)}


@app.before_request
def before_request():
    # don't allow anyone who isn't rentaflop to communicate with host daemon
    # only people who know a host's rentaflop id are the host and rentaflop
    request_json = request.get_json()
    request_rentaflop_id = request_json.get("rentaflop_id", "")
    if request_rentaflop_id != RENTAFLOP_ID:
        return abort(403)
    
    # force https
    if not request.is_secure:
        url = request.url.replace('http://', 'https://', 1)
        code = 301

        return redirect(url, code=code)


def run_flask_server(q):
    @app.route("/", methods=["POST"])
    def index():
        request_json = request.get_json()
        cmd = request_json.get("cmd")
        params = request_json.get("params")
        func = CMD_TO_FUNC.get(cmd)
        finished = False
        if func:
            try:
                finished = log_before_after(func, params)()
            except Exception as e:
                DAEMON_LOGGER.exception(f"Caught exception: {e}")
        if finished is True:
            q.put(finished)
        # finished isn't True but it's not Falsey, so return it in response
        if (finished is not True) and finished:
            return jsonify(finished), 200

        return jsonify("200")
    
    app.run(host='0.0.0.0', port=46443, ssl_context='adhoc')
    
    
CMD_TO_FUNC = {
    "mine": mine,
    "update": update,
    "uninstall": uninstall,
    "send_logs": send_logs,
    "status": status,
}
IGD = None
RENTAFLOP_ID = None


def main():
    _handle_startup()
    app.secret_key = uuid.uuid4().hex
    # run server, allowing it to shut itself down
    q = multiprocessing.Queue()
    server = multiprocessing.Process(target=run_flask_server, args=(q,))
    server.start()
    finished = q.get(block=True)
    if finished:
        server.terminate()
        DAEMON_LOGGER.debug("Stopping daemon.")
        logging.shutdown()


if __name__=="__main__":
    main()
