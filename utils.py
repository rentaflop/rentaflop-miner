"""
utility functions to be used in various parts of host software
"""
import subprocess
from config import DAEMON_LOGGER
import time
import os
import requests


def run_shell_cmd(cmd, quiet=False, format_output=True):
    """
    run cmd and log output
    """
    output = None
    if not quiet:
        DAEMON_LOGGER.debug(f'''Running command {cmd}...''')
    try:
        output = subprocess.check_output(cmd, shell=True, encoding="utf8", stderr=subprocess.STDOUT)
        formatted_output = output.replace("\n", " \\n ")
        if format_output:
            output = formatted_output
    except subprocess.CalledProcessError as e:
        # always print errors
        DAEMON_LOGGER.error(f"Exception: {e}\n{e.output}")
    if output and not quiet:
        DAEMON_LOGGER.debug(f'''Output for {cmd}: {formatted_output}''')

    return output


def log_before_after(func, params):
    """
    wrapper to log debug info before and after each daemon command
    """
    def wrapper():
        DAEMON_LOGGER.debug(f"Entering {func.__name__} with params {params}...")
        ret_val = func(params)
        DAEMON_LOGGER.debug(f"Exiting {func.__name__}.")

        return ret_val

    return wrapper


def get_igd():
    """
    returns internet gateway device URL for upnp to use
    """
    timeouts = 10
    for _ in range(timeouts):
        output = run_shell_cmd('upnpc -s | grep "Found valid IGD" | cut -d " " -f 5', format_output=False)
        if "No IGD UPnP Device found" in output:
            time.sleep(5)
            continue

        return output.replace("\n", "")

    if "No IGD UPnP Device found" in output:
        # TODO enter some sort of error state?
        return None


def get_gpus():
    """
    returns [gpu names], [corresponding gpu indexes] in order from lowest to highest index
    """
    gpu_info = run_shell_cmd("nvidia-smi --query-gpu=gpu_name,index --format=csv", format_output=False)
    gpu_info = gpu_info.split()[1:]
    gpu_names = []
    gpu_indexes = []
    for gpu in gpu_info:
        name, index = gpu.split(", ")
        gpu_names.append(name)
        gpu_indexes.append(int(index))

    # order both lists by index
    zipped_lists = zip(gpu_indexes, gpu_names)
    sorted_pairs = sorted(zipped_lists)
    tuples = zip(*sorted_pairs)
    gpu_indexes, gpu_names = [list(tuple) for tuple in tuples]
    gpu_indexes = [str(index) for index in gpu_indexes]
    
    return gpu_names, gpu_indexes


def get_state(igd=None):
    """
    returns a dictionary with all relevant daemon state information
    this includes gpus, running containers, container use, upnp ports, etc.
    igd is internet gateway device to speed up upnpc command
    """
    state = {}
    gpu_names, gpu_indexes = get_gpus()
    state["gpu_names"] = gpu_names
    n_gpus = len(gpu_names)
    state["n_gpus"] = str(n_gpus)
    gpu_states = {gpu_index:"stopped" for gpu_index in gpu_indexes}
    # get all container names
    containers = run_shell_cmd('docker ps --filter "name=rentaflop*" --format {{.Names}}', format_output=False).split()
    for container in containers:
        # container looks like f"rentaflop-sandbox-{gpu}-{mine_type}"
        _, _, gpu, mine_type = container.split("-")
        gpu_states[gpu] = mine_type

    state["gpu_states"] = gpu_states
    igd_flag = "" if not igd else f" -u {igd}"
    ports = run_shell_cmd(f'upnpc{igd_flag} -l | grep rentaflop | cut -d " " -f 4 | cut -d "-" -f 1', format_output=False).split()
    state["ports"] = ports

    return state


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
            data = get_state()
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


RENTAFLOP_ID = _get_registration()
