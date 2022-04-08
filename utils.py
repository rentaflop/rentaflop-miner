"""
utility functions to be used in various parts of host software
"""
import subprocess
from config import DAEMON_LOGGER
import time
import json
import requests


SUPPORTED_GPUS = [
    "NVIDIA GeForce GTX 1070 Ti",
    "NVIDIA GeForce GTX 1080",
    "NVIDIA GeForce GTX 1080 Ti",
    "NVIDIA GeForce RTX 2070 Super",
    "NVIDIA GeForce RTX 2080",
    "NVIDIA GeForce RTX 2080 Super",
    "NVIDIA GeForce RTX 2080 Ti",
    "NVIDIA GeForce RTX 3050",
    "NVIDIA GeForce RTX 3060",
    "NVIDIA GeForce RTX 3060 Ti",
    "NVIDIA GeForce RTX 3070",
    "NVIDIA GeForce RTX 3070 Ti",
    "NVIDIA GeForce RTX 3080",
    "NVIDIA GeForce RTX 3080 Ti",
    "NVIDIA GeForce RTX 3090",
]


def run_shell_cmd(cmd, quiet=False, very_quiet=False, format_output=True):
    """
    if quiet will only print errors, if very_quiet will silence everything including errors
    if not format_output will return exact cmd output
    run cmd and log output
    """
    if very_quiet:
        quiet = True
    output = None
    if not quiet:
        DAEMON_LOGGER.debug(f'''Running command {cmd}...''')
    try:
        output = subprocess.check_output(cmd, shell=True, encoding="utf8", stderr=subprocess.STDOUT)
        formatted_output = output.replace("\n", " \\n ")
        if format_output:
            output = formatted_output
    except subprocess.CalledProcessError as e:
        # print errors unless very quiet
        if not very_quiet:
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
    time_length = 1
    for _ in range(timeouts):
        output = run_shell_cmd('upnpc -s | grep "Found valid IGD" | cut -d " " -f 5', format_output=False)
        if "No IGD UPnP Device found" in output:
            time.sleep(time_length)
            time_length *= 2
            continue

        return output.replace("\n", "")

    if "No IGD UPnP Device found" in output:
        # TODO enter some sort of error state?
        return None


def get_gpus(available_resources, quiet=False):
    """
    returns [gpu names], [corresponding gpu indexes] in order from lowest to highest index
    """
    gpu_names = []
    gpu_indexes = available_resources["gpu_indexes"]
    for gpu_index in gpu_indexes:
        gpu_info = run_shell_cmd(f"nvidia-smi -i {gpu_index} --query-gpu=gpu_name --format=csv", quiet=quiet, format_output=False).split("\n")
        gpu_name = gpu_info[1]
        gpu_names.append(gpu_name)
    
    return gpu_names, gpu_indexes


def get_state(available_resources, igd=None, gpu_only=False, quiet=False):
    """
    returns a dictionary with all relevant daemon state information
    this includes gpus, running containers, container use, upnp ports, etc.
    igd is internet gateway device to speed up upnpc command
    gpu_only will determine whether to only get gpu-related info
    state looks like this:
    {
      "state": {
        "gpus": [
          {
            "index": "0",
            "name": "NVIDIA GeForce RTX 3080",
            "state": "gpc",
            "queue": [54, 118, 1937],
          },
          {
            "index": "1",
            "name": "NVIDIA GeForce RTX 3060 Ti",
            "state": "crypto"
            "queue": [],
          }
        ],
        "n_gpus": "2",
        "ports": [
          "46443",
          "46444"
        ],
        "resources": {
          "gpu_indexes": [
            "0",
            "1"
          ],
        },
        "version": "01e243e",
        "khs": 346.3, // total hash rate
        "stats": { 
          "hs": [123, 223.3], //array of hashes
          "hs_units": "khs", //Optional: units that are uses for hashes array, "hs", "khs", "mhs", ... Default "khs".   
          "temp": [60, 63], //array of miner temps
          "fan": [80, 100], //array of miner fans
          "uptime": 12313232, //seconds elapsed from miner stats
          "ver": "1.2.3.4-beta", //miner version currently run, parsed from it's api or manifest 
          "ar": [123, 3], //Optional: acceped, rejected shares 
          "algo": "customalgo", //Optional: algo used by miner, should one of the exiting in Hive
          "bus_numbers": [0, 1, 12, 13] //Pci buses array in decimal format. E.g. 0a:00.0 is 10
        }
      }
    }
    """
    state = {}
    gpu_names, gpu_indexes = get_gpus(available_resources, quiet)
    state["gpus"] = [{"index":gpu_index, "name": gpu_names[i], "state": "stopped", "queue": []} for i, gpu_index in enumerate(gpu_indexes)]
    n_gpus = len(gpu_names)
    state["n_gpus"] = str(n_gpus)
    # get all container names
    containers = run_shell_cmd('docker ps --filter "name=rentaflop*" --filter "ancestor=rentaflop/sandbox" --format {{.Names}}',
                               quiet=quiet, format_output=False).split()
    for container in containers:
        # container looks like f"rentaflop-sandbox-{gpu}"
        _, _, gpu = container.split("-")
        for i, gpu_dict in enumerate(state["gpus"]):
            if gpu_dict["index"] == gpu:
                # request queued jobs from docker
                container_ip = run_shell_cmd("docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "+container, format_output=False).strip()
                url = f"https://{container_ip}"
                data = {"cmd": "status", "params": {}}
                files = {'json': json.dumps(data)}
                result = requests.post(url, files=files, verify=False)
                container_queue = result.json().get("queue")
                container_state = "gpc" if container_queue else "crypto"
                state["gpus"][i]["state"] = container_state
                state["gpus"][i]["queue"] = container_queue

    if not gpu_only:
        igd_flag = "" if not igd else f" -u {igd}"
        ports = run_shell_cmd(f'upnpc{igd_flag} -l | grep rentaflop | cut -d "-" -f 1 | rev | cut -d " " -f 1 | rev', quiet=quiet, format_output=False).split()
        state["ports"] = ports
        state["version"] = run_shell_cmd("git rev-parse --short HEAD", quiet=quiet, format_output=False).replace("\n", "")
        state["resources"] = available_resources

    return state            


# find good open ports at https://stackoverflow.com/questions/10476987/best-tcp-port-number-range-for-internal-applications
_PORT_TYPE_TO_START = {
    "daemon": 46443,
}


def select_port(igd, port_type):
    """
    finds next available port by port_type and returns the number
    each type of port starts at a minimum number and ascends
    """
    selected_port = _PORT_TYPE_TO_START[port_type]
    ports_in_use = run_shell_cmd(f'upnpc -u {igd} -l | grep rentaflop | cut -d "-" -f 1 | rev | cut -d " " -f 1 | rev', format_output=False).split()
    while str(selected_port) in ports_in_use:
        selected_port += 1

    return selected_port
