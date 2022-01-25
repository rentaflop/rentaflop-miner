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
import multiprocessing
from flask import Flask, jsonify, request
from config import DAEMON_LOGGER, FIRST_STARTUP, LOG_FILE
from utils import run_shell_cmd, log_before_after


app = Flask(__name__)


def _first_startup():
    """
    run rentaflop installation and registration steps
    """
    daemon_py = os.path.realpath(__file__)
    # ensure daemon is run on system startup
    run_shell_cmd(f'(crontab -u root -l; echo "@reboot python3 {daemon_py}") | crontab -u root -')
    # perform system update
    update({"type": "system"}, reboot=False)
    # install dependencies
    run_shell_cmd("sudo apt-get install ca-certificates curl gnupg lsb-release python3-pip -y")
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
    run_shell_cmd('''sudo sed -i '$s/}/,\n"userns-remap":"default"}/' /etc/docker/daemon.json''')
    run_shell_cmd("sudo systemctl restart docker")
    run_shell_cmd("sudo docker build -f Dockerfile -t rentaflop/sandbox .")

    # register host with rentaflop
    
    run_shell_cmd("sudo reboot")

    
def _handle_startup():
    """
    checks to see if there's an existing log file to handle startup scenarios
    if no log file, then assume first startup
    if first startup, run rentaflop installation and registration steps
    if log file exists, check last command to see if it was an update
    if not update, assume crash and error state
    if update, log update completed
    """
    # ensure daemon flask server is accessible
    internal_ip = run_shell_cmd("hostname -I | awk '{print $1}'", format_output=False).replace("\n", "")
    run_shell_cmd(f"upnpc -a {internal_ip} 46443 46443 tcp")
    
    if FIRST_STARTUP:
        _first_startup()
        
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
        # error state
        DAEMON_LOGGER.debug(f"Daemon crashed.")
        return

    DAEMON_LOGGER.debug(f"Exiting update.")


def mine(params):
    """
    handle commands related to mining, whether crypto mining or guest "mining"
    params looks like {"type": "crypto" | "gpc"}
    """
    mine_type = params["type"]
    run_shell_cmd("sudo docker run --gpus all --device /dev/nvidia0:/dev/nvidia0 --device /dev/nvidiactl:/dev/nvidiactl \
    --device /dev/nvidia-modeset:/dev/nvidia-modeset --device /dev/nvidia-uvm:/dev/nvidia-uvm --device /dev/nvidia-uvm-tools:/dev/nvidia-uvm-tools \
    -p 2222:22 --rm --name rentaflop/sandbox -dt rentaflop/sandbox")
    # TODO figure out how to handle differences between crypto and guest sandbox while trying to keep them in same docker
    # if mine_type == "crypto":
    #     return
    # elif mine_type == "gpc":
    #     return


def update(params, reboot=True):
    """
    handle commands related to rentaflop software and system updates
    params looks like {"type": "rentaflop" | "system"}
    """
    update_type = params["type"]
    if update_type == "rentaflop":
        run_shell_cmd("git pull")
        # daemon will shut down (but not full system) so this ensures it starts back up again
        run_shell_cmd('echo "sleep 3; python3 daemon.py" | at now')

        return True
    elif update_type == "system":
        run_shell_cmd("sudo apt-get update -y")
        run_shell_cmd("DEBIAN_FRONTEND=noninteractive \
        sudo apt-get \
        -o Dpkg::Options::=--force-confold \
        -o Dpkg::Options::=--force-confdef \
        -y --allow-downgrades --allow-remove-essential --allow-change-held-packages \
        dist-upgrade")
        if reboot:
            run_shell_cmd("sudo reboot")
        

def uninstall(params):
    """
    uninstall rentaflop from this machine
    """
    # stop and remove all rentaflop docker containers and images
    run_shell_cmd('docker stop $(docker ps --filter "name=rentaflop/*" -q)')
    run_shell_cmd('docker rmi $(docker images -q "rentaflop/*") $(docker images "nvidia/cuda" -a -q)')
    # send logs first
    send_logs(params)
    # clean up rentaflop host software
    run_shell_cmd("upnpc -d 46443 tcp")
    daemon_py = os.path.realpath(__file__)
    run_shell_cmd(f"crontab -u root -l | grep -v 'python3 {daemon_py}' | crontab -u root -")
    run_shell_cmd("rm -rf ../rentaflop-host", True)

    return True


def send_logs(params):
    """
    gather host logs and send back to rentaflop servers
    """
    with open(LOG_FILE, "r") as f:
        logs = f.read()

    return {"logs": logs}


def run_flask_server(q):
    @app.route("/", methods=["POST"])
    def index():
        request_json = request.get_json()
        # TODO figure out a way to only run commands from rentaflop, perhaps using keys
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
    
    app.run(host='0.0.0.0', port=46443)
    
    
CMD_TO_FUNC = {
    "mine": mine,
    "update": update,
    "uninstall": uninstall,
    "send_logs": send_logs,
}


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
        logging.shutdown()


if __name__=="__main__":
    main()
