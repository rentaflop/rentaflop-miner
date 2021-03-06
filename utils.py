"""
utility functions to be used in various parts of host software
"""
import subprocess
from config import DAEMON_LOGGER, REGISTRATION_FILE
import time
import json
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import os
import tempfile
import copy


SUPPORTED_GPUS = {
    "NVIDIA GeForce GTX 1060",
    "NVIDIA GeForce GTX 1070",
    "NVIDIA GeForce GTX 1070 Ti",
    "NVIDIA GeForce GTX 1080",
    "NVIDIA GeForce GTX 1080 Ti",
    "NVIDIA GeForce RTX 2050",
    "NVIDIA GeForce RTX 2060",
    "NVIDIA GeForce RTX 2060 SUPER",
    "NVIDIA GeForce RTX 2070",
    "NVIDIA GeForce RTX 2070 SUPER",
    "NVIDIA GeForce RTX 2080",
    "NVIDIA GeForce RTX 2080 SUPER",
    "NVIDIA GeForce RTX 2080 Ti",
    "NVIDIA GeForce RTX 3050",
    "NVIDIA GeForce RTX 3050 Laptop GPU",
    "NVIDIA GeForce RTX 3050 Ti Laptop GPU",
    "NVIDIA GeForce RTX 3060",
    "NVIDIA GeForce RTX 3060 Laptop GPU",
    "NVIDIA GeForce RTX 3060 Ti",
    "NVIDIA GeForce RTX 3070",
    "NVIDIA GeForce RTX 3070 Laptop GPU",
    "NVIDIA GeForce RTX 3070 Ti",
    "NVIDIA GeForce RTX 3070 Ti Laptop GPU",
    "NVIDIA GeForce RTX 3080",
    "NVIDIA GeForce RTX 3080 Laptop GPU",
    "NVIDIA GeForce RTX 3080 Ti",
    "NVIDIA GeForce RTX 3080 Ti Laptop GPU",
    "NVIDIA GeForce RTX 3090",
    "NVIDIA GeForce RTX 3090 Ti",
}


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
        DAEMON_LOGGER.debug(f'''Output: {formatted_output}''')

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


def get_igd(quiet=False):
    """
    returns internet gateway device URL for upnp to use
    """
    # sleep for up to 96 seconds
    tries = 3
    time_length = 32
    is_first = True
    for _ in range(tries):
        if not is_first:
            time.sleep(time_length)
            time_length *= 2
        else:
            is_first = False
        
        output = run_shell_cmd('upnpc -s', format_output=False, quiet=quiet)
        if not output or "No IGD UPnP Device found" in output:
            continue

        candidate_igds = set()
        for word in output.split():
            if word.startswith("http") and "miniupnp" not in word:
                candidate_igds.add(word)

        for candidate in candidate_igds:
            # test out candidate igd url forwarding with test port to see if it works properly
            output = run_shell_cmd(f'upnpc -u {candidate} -e "rentaflop" -r 46442 tcp', format_output=False, quiet=quiet)
            run_shell_cmd(f"upnpc -u {candidate} -d 46442 tcp", format_output=False, quiet=quiet)
            if output and "is redirected to internal" in output:
                return candidate
        
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


def get_mining_stats(gpu):
    """
    return hash rate and gpu mining stats for gpu
    """
    khs = 0
    stats = "null"
    # 4059 is default port from hive
    crypto_port = 4059 + int(gpu)
    khs_stats = run_shell_cmd(f"./h-stats.sh {crypto_port}", format_output=False, quiet=True)
    if khs_stats:
        khs_stats = khs_stats.splitlines()
    if len(khs_stats) == 2:
        khs = float(khs_stats[0])
        stats = json.loads(khs_stats[1])
    
    # TODO if running gpc, apply rentaflop multiplier to estimate additional crypto earnings

    return khs, stats

    
def get_khs_stats(khs_vals, stats_vals):
    """
    combine khs and stats values from each GPU into one for host
    return khs, stats
    """
    khs = sum(khs_vals)
    stats = {"hs": [], "hs_units": "khs", "temp": [], "fan": [], "uptime": 0, "ver": "", "ar": [], "algo": "rentaflop", "bus_numbers": []}
    total_accepted = 0
    total_rejected = 0
    for stats_val in stats_vals:
        stats["hs"].extend(stats_val.get("hs", []))
        stats["temp"].extend(stats_val.get("temp", []))
        stats["fan"].extend(stats_val.get("fan", []))
        stats["bus_numbers"].extend(stats_val.get("bus_numbers", []))
        ar = stats_val.get("ar", [])
        if not ar:
            ar = [0, 0]
        total_accepted += ar[0]
        total_rejected += ar[1]
    
    stats["uptime"] = round(time.time() - _START_TIME)
    stats["ar"] = [total_accepted, total_rejected, 0, "0"]
    stats["total_khs"] = str(khs)

    return khs, stats    


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
    khs_vals = []
    stats_vals = []
    # get crypto mining state
    for i, gpu in enumerate(gpu_indexes):
        output = run_shell_cmd(f"nvidia-smi -i {gpu}", very_quiet=True)
        if "t-rex" in output:
            state["gpus"][i]["state"] = "crypto"
            khs_val, stats_val = get_mining_stats(gpu)
            khs_vals.append(khs_val)
            if isinstance(stats_val, str) and stats_val == "null":
                stats_val = {}
            stats_vals.append(stats_val)
        else:
            # TODO still return values times multiplier when renders or benchmarks are running
            khs_vals.append(0)
            stats_vals.append({})
    
    benchmark_containers = run_shell_cmd('docker ps --filter "name=rentaflop-benchmark*" --filter "ancestor=rentaflop/sandbox" --format {{.Names}}',
                               quiet=quiet, format_output=False).split()
    for container in benchmark_containers:
        # container looks like f"rentaflop-sandbox-{gpu}"
        _, _, gpu = container.split("-")
        for i, gpu_dict in enumerate(state["gpus"]):
            if gpu_dict["index"] == gpu:
                # treat benchmark jobs as gpc
                state["gpus"][i]["state"] = "gpc"
    
    # get all sandbox container names
    sandbox_containers = run_shell_cmd('docker ps --filter "name=rentaflop-sandbox*" --filter "ancestor=rentaflop/sandbox" --format {{.Names}}',
                               quiet=quiet, format_output=False).split()
    for container in sandbox_containers:
        # container looks like f"rentaflop-sandbox-{gpu}"
        _, _, gpu = container.split("-")
        for i, gpu_dict in enumerate(state["gpus"]):
            if gpu_dict["index"] == gpu:
                # request queued jobs from docker
                container_ip = run_shell_cmd("docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "+container, format_output=False, quiet=quiet).strip()
                url = f"https://{container_ip}"
                data = {"cmd": "status", "params": {}}
                files = {'json': json.dumps(data)}
                result = post_to_sandbox(url, files, quiet=True)
                if not result:
                    result = {"queue": []}
                
                container_queue = result.get("queue")
                container_state = "stopped"
                if container_queue:
                    container_state = "gpc"
                    
                state["gpus"][i]["state"] = container_state
                state["gpus"][i]["queue"] = container_queue

    if not gpu_only:
        ports = []
        if igd:
            igd_flag = "" if not igd else f" -u {igd}"
            ports = run_shell_cmd(f'upnpc{igd_flag} -l | grep rentaflop | cut -d "-" -f 1 | rev | cut -d " " -f 1 | rev', quiet=quiet, format_output=False).split()
        state["ports"] = ports
        state["version"] = run_shell_cmd("git rev-parse --short HEAD", quiet=quiet, format_output=False).replace("\n", "")
        state["resources"] = available_resources
        khs, stats = get_khs_stats(khs_vals, stats_vals)
        state["khs"] = khs
        state["stats"] = stats

    return state            


# find good open ports at https://stackoverflow.com/questions/10476987/best-tcp-port-number-range-for-internal-applications
_PORT_TYPE_TO_START = {
    "daemon": 46443,
}
_START_TIME = time.time()


def select_port(igd, port_type):
    """
    finds next available port by port_type and returns the number
    each type of port starts at a minimum number and ascends
    """
    selected_port = _PORT_TYPE_TO_START[port_type]
    # if upnp not available, we use starting port type for every rig and user manually forwards 46443->rig1:46443, 46444->rig2:46443, etc
    # TODO create config param for non upnp users to tell rentaflop servers which port to request
    if igd:
        ports_in_use = run_shell_cmd(f'upnpc -u {igd} -l | grep rentaflop | cut -d "-" -f 1 | rev | cut -d " " -f 1 | rev', format_output=False).split()
        while str(selected_port) in ports_in_use:
            selected_port += 1

    return selected_port


def kill_other_daemons():
    """
    kill all other processes running daemon.py
    """
    daemons = run_shell_cmd('ps aux | grep "daemon.py" | grep -v grep', very_quiet=True, format_output=False).splitlines()
    current_pid = os.getpid()
    pids_to_kill = [daemon.split()[1] for daemon in daemons if daemon.split()[1] != current_pid]
    run_shell_cmd(f'kill -9 {" ".join(pids_to_kill)}', very_quiet=True)


def get_custom_config():
    """
    parse and return important values from wallet.conf
    """
    with open("/hive-config/wallet.conf", "r") as f:
        config_vals = f.read().splitlines()

    custom_user_config = ""
    custom_template = ""
    for config_val in config_vals:
        if config_val.startswith("CUSTOM_USER_CONFIG="):
            custom_user_config = config_val.replace("CUSTOM_USER_CONFIG=", "").replace("'", "")
        elif config_val.startswith("CUSTOM_TEMPLATE="):
            custom_template = config_val.replace("CUSTOM_TEMPLATE=", "").replace('"', "")

    wallet_address = custom_template.split(".")[0]
    email = ""
    custom_values = custom_user_config.split(";")
    for custom_value in custom_values:
        if custom_value.startswith("EMAIL="):
            email = custom_value.replace("EMAIL=", "")

    return email, wallet_address


def post_to_daemon(data):
    """
    make post request to rentaflop servers daemon endpoint
    catch exceptions resulting from request
    """
    daemon_url = "https://portal.rentaflop.com/api/host/daemon"
    DAEMON_LOGGER.debug(f"Sent to /api/host/daemon: {data}")
    try:
        response = requests.post(daemon_url, json=data)
        response_json = response.json()
    except (requests.exceptions.ConnectionError, json.decoder.JSONDecodeError) as e:
        DAEMON_LOGGER.error(f"Exception during post request: {e}")

        return {}
    
    DAEMON_LOGGER.debug(f"Received from /api/host/daemon: {response.status_code} {response_json}")

    return response_json


def post_to_sandbox(sandbox_url, data, quiet=False):
    """
    make post request to docker sandbox servers; do retries since container may have just been started
    catch exceptions resulting from request
    """
    if not quiet:
        DAEMON_LOGGER.debug(f"Sent to sandbox {sandbox_url}: {data}")
    tries = 3
    for _ in range(tries):
        try:
            response = requests.post(sandbox_url, files=data, verify=False)
            response_json = response.json()
            if response_json:
                break
        except (requests.exceptions.ConnectionError, json.decoder.JSONDecodeError) as e:
            if not quiet:
                DAEMON_LOGGER.error(f"Exception during post request: {e}")
            response_json = {}
            time.sleep(1)

    if not quiet:
        DAEMON_LOGGER.debug(f"Received from sandbox {sandbox_url}: {response_json}")

    return response_json


def update_config(rentaflop_id=None, wallet_address=None, daemon_port=None, email=None, sandbox_id=None):
    """
    update rentaflop config file with new values
    """
    is_changed = False
    rentaflop_config = {}
    with open(REGISTRATION_FILE, "r") as f:
        rentaflop_config = json.load(f)
        if rentaflop_id and rentaflop_id != rentaflop_config.get("rentaflop_id", ""):
            rentaflop_config["rentaflop_id"] = rentaflop_id
            is_changed = True
        if wallet_address and wallet_address != rentaflop_config.get("wallet_address", ""):
            rentaflop_config["wallet_address"] = wallet_address
            is_changed = True
        if daemon_port and daemon_port != rentaflop_config.get("daemon_port", 0):
            rentaflop_config["daemon_port"] = daemon_port
            is_changed = True
        if email and email != rentaflop_config.get("email", ""):
            rentaflop_config["email"] = email
            is_changed = True
        if sandbox_id and sandbox_id != rentaflop_config.get("sandbox_id", ""):
            rentaflop_config["sandbox_id"] = sandbox_id
            is_changed = True

    if is_changed:
        with open(REGISTRATION_FILE, "w") as f:
            f.write(json.dumps(rentaflop_config, indent=4, sort_keys=True))


def install_or_update_crypto_miner():
    """
    check for crypto miner installation and install if not found
    update version if installed and not up to date; does nothing if installed and up to date
    """
    target_version = "0.26.4"
    if os.path.exists("trex"):
        current_version = run_shell_cmd('trex/t-rex --version | cut -d " " -f 5', format_output=False).strip()
        # already up to date so do nothing
        if current_version == "v" + target_version:
            return

        # need to reinstall with target version, so remove current installation
        run_shell_cmd("rm -rf trex")

    DAEMON_LOGGER.debug(f"Installing crypto miner version {target_version}...")
    # go to https://trex-miner.com/ to check trex version updates
    run_shell_cmd(f"curl -L https://trex-miner.com/download/t-rex-{target_version}-linux.tar.gz > trex.tgz && mkdir trex && tar -xzf trex.tgz -C trex && rm trex.tgz")


def stop_crypto_miner(gpu):
    """
    stop crypto miner on gpu
    """
    # find t-rex pid running on this specific gpu and kill it to stop crypto mining
    # can't use signal 9 to kill because it causes GPU errors
    run_shell_cmd(f"nvidia-smi -i {gpu} | grep 't-rex' | " + "awk '{ print $5 }' | xargs -n1 kill -15", very_quiet=True)


def start_crypto_miner(gpu, crypto_port, wallet_address, hostname, mining_algorithm, pool_url):
    """
    start crypto miner on gpu; do nothing if already running
    """
    # do nothing if running
    output = run_shell_cmd(f"nvidia-smi -i {gpu} | grep 't-rex'", very_quiet=True)
    if output:
        return
    
    # create temp config file, run miner, then delete file
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        config_file = tmp.name
        with open("config.json", "r") as f:
            config_json = json.load(f)
        pools = config_json["pools"][0]
        pools["user"] = wallet_address
        pools["url"] = pool_url
        pools["worker"] = f"{hostname}_{gpu}"
        config_json["algo"] = mining_algorithm

    with open(config_file, "w") as f:
        json.dump(config_json, f)

    # run miner
    os.system(f"./trex/t-rex -c {config_file} --api-bind-http 127.0.0.1:{crypto_port} -d {gpu} &")

    # clean up tmp file after 60 seconds without hangup
    run_shell_cmd(f'echo "sleep 60; rm {config_file}" | at now', quiet=True)


def check_correct_driver(reboot=True):
    """
    check for correct driver version
    install if not found, otherwise do nothing
    reboot is required after changing drivers; reboot option toggles this action within this function
    """
    target_version = "510"
    # check if installed
    output = run_shell_cmd(f"dpkg -l | grep nvidia-driver-{target_version}")
    if output:
        return

    # not installed so uninstall existing and install target
    run_shell_cmd("nvidia-uninstall -s")
    run_shell_cmd(f"apt install nvidia-driver-{target_version} -y")
    if reboot:
        run_shell_cmd("sudo reboot")


def wait_for_sandbox_server(container_ip):
    """
    wait up to 30 seconds for sandbox server to start
    if still not up after 30 seconds, return and hope for the best
    """
    sandbox_url = f"https://{container_ip}/health"
    tries = 30
    for _ in range(tries):
        try:
            response = requests.get(sandbox_url, verify=False)
            if response.status_code == 200:
                return
        except (requests.exceptions.ConnectionError, json.decoder.JSONDecodeError) as e:
            pass

        time.sleep(1)


def get_oc_settings():
    """
    read and return currently-set overclock settings and associated hash
    """
    oc_file = os.getenv("NVIDIA_OC_CONF")
    current_oc_settings = {}
    file_contents = None
    try:
        with open(oc_file, "r") as f:
            file_contents = f.read()
            for line in file_contents.splitlines():
                (key, val) = line.replace('"', "").replace("\n", "").split("=")
                current_oc_settings[key] = val
    except FileNotFoundError:
        # return None if overclocking not set
        return None, hash(file_contents)
    
    return current_oc_settings, hash(file_contents)


def _get_setting_from_key(oc_settings, key, n_gpus):
    setting = oc_settings[key].split()
    if not setting:
        setting = ["0"]*n_gpus

    return setting


def _replace_settings(n_gpus, oc_settings, gpu_indexes, key, values):
    """
    replace oc_settings for key at gpu_indexes with values
    """
    new_settings = _get_setting_from_key(oc_settings, key, n_gpus)
    for i, gpu in enumerate(gpu_indexes):
        new_settings[gpu] = values[i]

    oc_settings[key] = " ".join(new_settings)


def _write_settings(new_oc_settings):
    """
    write and set oc_settings
    """
    # since we're about to do a write to oc file, we must check to see if it's changed by user and reset
    oc_file = os.getenv("NVIDIA_OC_CONF")
    with open(oc_file, "w") as f:
        to_write = ""
        for k in new_oc_settings:
            to_write += f'{k}="{new_oc_settings[k]}"\n'
        
        f.write(to_write)

    run_shell_cmd("nvidia-oc", quiet=True)


def disable_oc(gpu_indexes):
    """
    reset overclock settings for gpus at gpu indexes
    leave power limit settings alone so as to not cause overheating; overclock alone causes issues with rendering
    """
    original_oc_settings, oc_hash = read_oc_file()
    current_oc_settings, current_oc_hash = get_oc_settings()
    # do nothing if overclocking not set
    if not current_oc_settings:
        return
    
    new_oc_settings = copy.deepcopy(current_oc_settings)
    # find n_gpus this way because there might be unsupported gpus present that hive supports
    n_gpus = max([len(new_oc_settings[k].split()) for k in new_oc_settings])
    # do nothing if 0 because it means none of the supported gpus are overclocked anyways
    if n_gpus == 0:
        return

    is_different = _check_hash_difference(current_oc_settings, oc_hash, current_oc_hash)
    if is_different:
        original_oc_settings = current_oc_settings
    # setting values to 0 does a reset to default OC settings
    new_values = ["0"]*len(gpu_indexes)
    _replace_settings(n_gpus, new_oc_settings, gpu_indexes, "CLOCK", new_values)
    _replace_settings(n_gpus, new_oc_settings, gpu_indexes, "MEM", new_values)
    _replace_settings(n_gpus, new_oc_settings, gpu_indexes, "FAN", new_values)
    _write_settings(new_oc_settings)
    _, new_oc_hash = get_oc_settings()
    write_oc_file(original_oc_settings, new_oc_hash)


def enable_oc(gpu_indexes):
    """
    set overclock settings to original oc_settings
    """
    original_oc_settings, oc_hash = read_oc_file()
    current_oc_settings, current_oc_hash = get_oc_settings()
    # do nothing if overclocking not set
    if not current_oc_settings or not original_oc_settings:
        return

    is_different = _check_hash_difference(current_oc_settings, oc_hash, current_oc_hash)
    # do nothing if user set new oc settings, since we assume these are already enabled
    if is_different:
        return
    new_oc_settings = copy.deepcopy(current_oc_settings)
    # find n_gpus this way because there might be unsupported gpus present that hive supports
    n_gpus = max([len(new_oc_settings[k].split()) for k in new_oc_settings])
    original_clock_values = _get_setting_from_key(original_oc_settings, "CLOCK", n_gpus)
    original_clock_values = [original_clock_values[idx] for idx in gpu_indexes]
    original_mem_values = _get_setting_from_key(original_oc_settings, "MEM", n_gpus)
    original_mem_values = [original_mem_values[idx] for idx in gpu_indexes]
    original_fan_values = _get_setting_from_key(original_oc_settings, "FAN", n_gpus)
    original_fan_values = [original_fan_values[idx] for idx in gpu_indexes]
    _replace_settings(n_gpus, new_oc_settings, gpu_indexes, "CLOCK", original_clock_values)
    _replace_settings(n_gpus, new_oc_settings, gpu_indexes, "MEM", original_mem_values)
    _replace_settings(n_gpus, new_oc_settings, gpu_indexes, "FAN", original_fan_values)
    _write_settings(new_oc_settings)
    # original oc settings not overwritten, but we just overwrote oc file so need to update to new hash
    _, new_oc_hash = get_oc_settings()
    write_oc_file(original_oc_settings, new_oc_hash)


def write_oc_file(oc_settings, oc_hash):
    """
    write oc settings and hash to tmp file
    """
    oc_tmp_file = "/tmp/oc_file.json"
    with open(oc_tmp_file, "w") as f:
        oc_dict = {"oc_settings": oc_settings, "oc_hash": oc_hash}
        json.dump(oc_dict, f)


def read_oc_file():
    """
    read oc settings and hash from tmp file
    """
    oc_tmp_file = "/tmp/oc_file.json"
    with open(oc_tmp_file, "r") as f:
        oc_dict = json.load(f)

    return oc_dict["oc_settings"], oc_dict["oc_hash"]


def _check_hash_difference(new_oc_settings, original_oc_hash, new_oc_hash):
    """
    check if oc file was modified by another program prior to disabling oc. If so, we save changes to oc tmp file
    return boolean indicating whether difference found
    """
    if original_oc_hash != new_oc_hash:
        DAEMON_LOGGER.info(f"Detected changes to OC settings: {new_oc_settings}")
        write_oc_file(new_oc_settings, new_oc_hash)

        return True

    return False
