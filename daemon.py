"""
host daemon that communicates with rentaflop's servers for instructions
functions include, but are not limited to, software updates, system updates,
guest and crypto mining session initiation/termination, uninstallation
usage:
    python daemon.py
"""
import os


def _run_shell_cmd(cmd):
    """
    run cmd and log output
    """
    os.system(cmd)

    
def mine(params):
    """
    handle commands related to mining, whether crypto mining or guest "mining"
    """
    pass


def update(params):
    """
    handle commands related to rentaflop software and system updates
    params looks like {"type": "rentaflop" | "system"}
    """
    update_type = params["type"]
    if update_type == "rentaflop":
        _run_shell_cmd("git pull")
    elif update_type == "system":
        _run_shell_cmd("sudo apt-get update -y")
        _run_shell_cmd("DEBIAN_FRONTEND=noninteractive \
        sudo apt-get \
        -o Dpkg::Options::=--force-confold \
        -o Dpkg::Options::=--force-confdef \
        -y --allow-downgrades --allow-remove-essential --allow-change-held-packages \
        dist-upgrade")

    # daemon will shut down so ensure it starts back up again
    _run_shell_cmd('echo "sleep 5; python3 daemon.py" | at now')

    return True


def uninstall(params):
    """
    uninstall rentaflop from this machine
    """
    # stop and remove all rentaflop docker containers and images
    # TODO rename images/containers
    _run_shell_cmd('docker stop $(docker ps --filter "name=ssh*" -q)')
    _run_shell_cmd('docker rmi $(docker images -q "dasokol/*") $(docker images "nvidia/cuda" -a -q)')

    return True


def send_logs(params):
    """
    gather host logs and send back to rentaflop servers
    """
    pass


def main():
    cmd_to_func = {
        "mine": mine,
        "update": update,
        "uninstall": uninstall,
        "send_logs": send_logs,
    }
    finished = False
    while not finished:
        # TODO either receive command or request it
        response = {"cmd": "mine", "params": {}}
        cmd = response["cmd"]
        params = response["params"]
        if cmd in cmd_to_func:
            # TODO try except here
            finished = cmd_to_func[cmd](params)


if __name__=="__main__":
    main()
