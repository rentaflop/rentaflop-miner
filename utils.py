"""
utility functions to be used in various parts of host software
"""
import subprocess
from config import DAEMON_LOGGER


def run_shell_cmd(cmd, quiet=False, format_output=True):
    """
    run cmd and log output
    """
    output = None
    if not quiet:
        DAEMON_LOGGER.debug(f'''Running command {cmd}...''')
    try:
        output = subprocess.check_output(cmd, shell=True, encoding="utf8", stderr=subprocess.STDOUT)
        if format_output:
            output = output.replace("\n", " \\n ")
    except subprocess.CalledProcessError as e:
        # always print errors
        DAEMON_LOGGER.error(f"Exception: {e}\n{e.output}")
    if output and not quiet:
        DAEMON_LOGGER.debug(f'''Output for {cmd}: {output}''')

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
    return run_shell_cmd('upnpc -s | grep "Found valid IGD" | cut -d " " -f 5', format_output=False).replace("\n", "")


def get_num_gpus():
    """
    returns the number of gpus available
    """
    return int(run_shell_cmd("nvidia-smi -L | wc -l", format_output=False))


def get_state():
    """
    returns a dictionary with all relevant daemon state information
    this includes gpus, running containers, container use, upnp ports, etc.
    """
    state = {}
    n_gpus = get_num_gpus()
    state["n_gpus"] = n_gpus
    gpu_states = {str(gpu):"down" for gpu in range(n_gpus)}
    # get all container names
    containers = run_shell_cmd('docker ps --filter "name=rentaflop*" --format {{.Names}}', format_output=False).split()
    for container in containers:
        # container looks like f"rentaflop-sandbox-{gpu}-{mine_type}"
        _, _, gpu, mine_type = container.split("-")
        gpu_states[gpu] = mine_type

    state["gpu_states"] = gpu_states
    ports = run_shell_cmd('upnpc -l | grep rentaflop | cut -d " " -f 4 | cut -d "-" -f 1', format_output=False).split()
    state["ports"] = ports

    return state
