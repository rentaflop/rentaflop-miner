"""
utility functions to be used in various parts of host software
"""
import subprocess
from config import DAEMON_LOGGER
import time


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


def get_gpus(quiet=False):
    """
    returns [gpu names], [corresponding gpu indexes] in order from lowest to highest index
    """
    gpu_info = run_shell_cmd("nvidia-smi --query-gpu=gpu_name,index --format=csv", quiet=quiet, format_output=False).split("\n")
    gpu_info = [gpu for gpu in gpu_info if gpu]
    gpu_info = gpu_info[1:]
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


def get_state(igd=None, gpu_only=False, quiet=False):
    """
    returns a dictionary with all relevant daemon state information
    this includes gpus, running containers, container use, upnp ports, etc.
    igd is internet gateway device to speed up upnpc command
    gpu_only will determine whether to only get gpu-related info
    """
    state = {}
    gpu_names, gpu_indexes = get_gpus(quiet)
    state["gpu_names"] = {gpu_index: gpu_names[i] for i, gpu_index in enumerate(gpu_indexes)}
    n_gpus = len(gpu_names)
    state["n_gpus"] = str(n_gpus)
    gpu_states = {gpu_index: "stopped" for gpu_index in gpu_indexes}
    # get all container names
    containers = run_shell_cmd('docker ps --filter "name=rentaflop*" --filter "ancestor=rentaflop/sandbox" --format {{.Names}}',
                               quiet=quiet, format_output=False).split()
    for container in containers:
        # container looks like f"rentaflop-sandbox-{mine_type}-{gpu}-{jupyter_port}-{ssh_port}"
        _, _, mine_type, gpu, _, _ = container.split("-")
        gpu_states[gpu] = mine_type

    state["gpu_states"] = gpu_states
    if not gpu_only:
        igd_flag = "" if not igd else f" -u {igd}"
        # TODO keep track of ports for this host specifically and only return those
        ports = run_shell_cmd(f'upnpc{igd_flag} -l | grep rentaflop | cut -d " " -f 4 | cut -d "-" -f 1', quiet=quiet, format_output=False).split()
        state["ports"] = ports
        state["version"] = run_shell_cmd("git rev-parse --short HEAD", quiet=quiet, format_output=False).replace("\n", "")

    return state            


_PORT_TYPE_TO_START = {
    "daemon": 46443,
    "jupyter": 46880,
    "ssh": 46422
}


def select_port(igd, port_type):
    """
    finds next available port by port_type and returns the number
    each type of port starts at a minimum number and ascends
    """
    selected_port = _PORT_TYPE_TO_START[port_type]
    ports_in_use = run_shell_cmd(f'upnpc -u {igd} -l | grep rentaflop | cut -d " " -f 4 | cut -d "-" -f 1', format_output=False).split()
    while str(selected_port) in ports_in_use:
        selected_port += 1

    return selected_port
