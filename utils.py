"""
utility functions to be used in various parts of host software
"""
import subprocess
from config import DAEMON_LOGGER, REGISTRATION_FILE, app, db, Overclock
import time
import json
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import os
import tempfile
import copy
import socket
import math
import glob
import datetime as dt


# look up series here https://en.wikipedia.org/wiki/GeForce_40_series
# https://www.nvidia.com/download/index.aspx
# NOTE: duplicated in backend config.py
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
    "NVIDIA GeForce RTX 4070 Ti",
    "NVIDIA GeForce RTX 4080",
    "NVIDIA GeForce RTX 4090",
}
CRYPTO_STATS = {"total_khs": "0.0"}
# NOTE: if updated, also update daemon.py, launchpad.js, and host_update lambda
TEST_HOSTS = ["rentaflop-one", "rentaflop-two", "rentaflop-three"]
# from https://docs.blender.org/manual/en/2.80/files/media/video_formats.html
VIDEO_FORMATS = [".mpg", ".mpeg", ".dvd", ".vob", ".mp4", ".avi", ".mov", ".dv", ".ogg", ".ogv", ".mkv", ".flv"]


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


def get_mining_stats():
    """
    return hash rate and gpu mining stats for gpus
    """
    khs = 0
    stats = "null"
    # 4059 is default port from hive
    crypto_port = 4059
    khs_stats = run_shell_cmd(f"./h-stats.sh {crypto_port}", format_output=False, quiet=True)
    if khs_stats:
        khs_stats = khs_stats.splitlines()
    if len(khs_stats) == 2:
        khs = float(khs_stats[0])
        stats = json.loads(khs_stats[1])
    
    # TODO if running gpc, apply rentaflop multiplier to estimate additional crypto earnings

    return khs, stats


def get_state(available_resources, queue_status, gpu_only=False, quiet=False, version=None, algo=None):
    """
    returns a dictionary with all relevant daemon state information
    this includes gpus, running tasks, etc.
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
            "last_frame_completed": 57,
            "first_frame_time": 12.34,
            "subsequent_frames_avg": 9.76,
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
    global CRYPTO_STATS
    state = {}
    gpu_indexes = available_resources["gpu_indexes"]
    gpu_names = available_resources["gpu_names"]
    state["gpus"] = [{"index": gpu_index, "name": gpu_names[i]} for i, gpu_index in enumerate(gpu_indexes)]
    n_gpus = len(gpu_names)
    state["n_gpus"] = str(n_gpus)
    state["status"] = "stopped"
    state["queue"] = []
    khs = 0
    stats = {}
    if not version:
        version = run_shell_cmd("git rev-parse --short HEAD", quiet=quiet, format_output=False).replace("\n", "")
    if not algo:
        algo = "rentaflop"
    # get crypto mining state
    output = run_shell_cmd(f"nvidia-smi", very_quiet=True)
    if "t-rex" in output:
        state["status"] = "crypto"
        khs, stats = get_mining_stats()
    else:
        # TODO still return values times multiplier when renders or benchmarks are running
        pass

    if stats != "null":
        stats["uptime"] = round(time.time() - _START_TIME)
        stats["algo"] = algo
        stats["ver"] = version
        # currently mining crypto and found higher stats so we save these to be displayed to hive during non-crypto mining tasks
        if "total_khs" in stats and float(stats["total_khs"]) > float(CRYPTO_STATS["total_khs"]):
            CRYPTO_STATS = stats
        
    # get task queue status
    result = queue_status({}) if (isinstance(stats, dict) and stats.get("uptime", 0) > 10) else {}
    task_queue = result.get("queue")
    last_frame_completed = result.get("last_frame_completed")
    first_frame_time = result.get("first_frame_time")
    subsequent_frames_avg = result.get("subsequent_frames_avg")
    # check for existing queue items
    if task_queue:
        state["status"] = "gpc"
        state["queue"] = task_queue
    if last_frame_completed is not None:
        state["last_frame_completed"] = last_frame_completed
    if first_frame_time:
        state["first_frame_time"] = first_frame_time
    if subsequent_frames_avg:
        state["subsequent_frames_avg"] = subsequent_frames_avg

    # if we're not mining crypto and crypto_stats is set, show saved crypto_stats
    if state["status"] != "crypto" and float(CRYPTO_STATS["total_khs"]) > 0.0:
        stats = CRYPTO_STATS

    if not gpu_only:
        state["version"] = version
        state["resources"] = {"gpu_indexes": available_resources["gpu_indexes"]}
        state["khs"] = khs
        state["stats"] = stats

    return state            


_START_TIME = time.time()


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
    # default pool url and hash alg
    pool_url = "eth.hiveon.com:4444"
    hash_algorithm = "ethash"
    custom_pass = ""
    for config_val in config_vals:
        if config_val.startswith("CUSTOM_USER_CONFIG="):
            custom_user_config = config_val.replace("CUSTOM_USER_CONFIG=", "").replace("'", "")
        elif config_val.startswith("CUSTOM_TEMPLATE="):
            custom_template = config_val.replace("CUSTOM_TEMPLATE=", "").replace('"', "")
        elif config_val.startswith("CUSTOM_URL="):
            pool_url_val = config_val.replace("CUSTOM_URL=", "").replace('"', "")
            # make sure there's actually a value set
            if pool_url_val:
                pool_url = pool_url_val
        elif config_val.startswith("CUSTOM_ALGO="):
            hash_algorithm_val = config_val.replace("CUSTOM_ALGO=", "").replace('"', "")
            # make sure there's actually a value set
            if hash_algorithm_val:
                hash_algorithm = hash_algorithm_val
        elif config_val.startswith("CUSTOM_PASS="):
            custom_pass = config_val.replace("CUSTOM_PASS=", "").replace('"', "")

    wallet_address = custom_template.split(".")[0]
    email = ""
    disable_crypto = False
    crypto_miner_config = ""
    task_miner_currency = ""
    # parse custom config args from flight sheet
    custom_values = custom_user_config.replace("; ", ";").replace("<", "").replace(">", "").split(";")
    for custom_value in custom_values:
        if custom_value.startswith("EMAIL="):
            email = custom_value.replace("EMAIL=", "")
        elif custom_value.startswith("DISABLE_CRYPTO") and "false" not in custom_value.lower():
            disable_crypto = True
        elif custom_value.startswith("CRYPTO_MINER_CONFIG="):
            crypto_miner_config = custom_value.replace("CRYPTO_MINER_CONFIG=", "")
        elif custom_value.startswith("TASK_MINER_CURRENCY="):
            task_miner_currency = custom_value.replace("TASK_MINER_CURRENCY=", "")

    return email, disable_crypto, wallet_address, pool_url, hash_algorithm, custom_pass, crypto_miner_config, task_miner_currency


def post_to_rentaflop(data, endpoint, quiet=False):
    """
    make post request to specified rentaflop server host endpoint
    catch exceptions resulting from request
    """
    rentaflop_url = f"https://portal.rentaflop.com/api/host/{endpoint}"
    if not quiet:
        DAEMON_LOGGER.debug(f"Sent to /api/host/{endpoint}: {data}")
    try:
        response = requests.post(rentaflop_url, json=data)
        response_json = response.json()
    except (requests.exceptions.ConnectionError, json.decoder.JSONDecodeError) as e:
        DAEMON_LOGGER.error(f"Exception during post request: {e}")

        return None

    if not quiet:
        DAEMON_LOGGER.debug(f"Received from /api/host/{endpoint}: {response.status_code} {response_json}")

    return response_json


def update_config(rentaflop_id=None, daemon_port=None, sandbox_id=None, wallet_address=None, email=None, task_miner_currency=None):
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
        if daemon_port and daemon_port != rentaflop_config.get("daemon_port", 0):
            rentaflop_config["daemon_port"] = daemon_port
            is_changed = True
        if sandbox_id and sandbox_id != rentaflop_config.get("sandbox_id", ""):
            rentaflop_config["sandbox_id"] = sandbox_id
            is_changed = True
        if wallet_address and wallet_address != rentaflop_config.get("wallet_address", ""):
            rentaflop_config["wallet_address"] = wallet_address
            is_changed = True
        if email and email != rentaflop_config.get("email", ""):
            rentaflop_config["email"] = email
            is_changed = True
        if task_miner_currency and task_miner_currency != rentaflop_config.get("task_miner_currency", ""):
            rentaflop_config["task_miner_currency"] = task_miner_currency
            is_changed = True

    if is_changed:
        with open(REGISTRATION_FILE, "w") as f:
            f.write(json.dumps(rentaflop_config, indent=4, sort_keys=True))


def install_or_update_crypto_miner():
    """
    check for crypto miner installation and install if not found
    update version if installed and not up to date; does nothing if installed and up to date
    """
    target_version = "0.26.8"
    if os.path.exists("trex"):
        current_version = run_shell_cmd('trex/t-rex --version | cut -d " " -f 5', format_output=False).strip()
        # already up to date so do nothing
        if current_version == "v" + target_version:
            return

        # need to reinstall with target version, so remove current installation
        run_shell_cmd("rm -rf trex")

    DAEMON_LOGGER.debug(f"Installing crypto miner version {target_version}...")
    # go to https://github.com/trexminer/T-Rex/releases to check trex version updates
    run_shell_cmd(f"curl -L https://github.com/trexminer/T-Rex/releases/download/0.26.8/t-rex-{target_version}-linux.tar.gz > trex.tgz && mkdir trex && tar -xzf trex.tgz -C trex && rm trex.tgz")


def stop_crypto_miner():
    """
    stop crypto miner
    """
    # don't use signal 9 to kill because it causes GPU errors (defaults to signal 15)
    run_shell_cmd('pkill -f t-rex')


def start_crypto_miner(crypto_port, hostname, crypto_config):
    """
    start crypto miner on gpus; do nothing if already running
    """
    # do nothing if running
    output = run_shell_cmd(f"nvidia-smi | grep 't-rex'", very_quiet=True)
    if output:
        return
    
    # create temp config file, run miner, then delete file
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        config_file = tmp.name
        with open("config.json", "r") as f:
            config_json = json.load(f)
        pools = config_json["pools"][0]
        # user task miner address as default, but cli args in crypto_miner_config will overwrite this if present
        pools["user"] = crypto_config["wallet_address"]
        pools["url"] = crypto_config["pool_url"]
        pools["pass"] = crypto_config["pass"]
        pools["worker"] = hostname
        config_json["algo"] = crypto_config["hash_algorithm"]

    with open(config_file, "w") as f:
        json.dump(config_json, f)

    crypto_miner_config = crypto_config["crypto_miner_config"]
    # run miner
    os.system(f"./trex/t-rex -c {config_file} {crypto_miner_config} --api-bind-http 127.0.0.1:{crypto_port} &")

    # clean up tmp file after 60 seconds without hangup
    run_shell_cmd(f'echo "sleep 60; rm {config_file}" | at now', quiet=True)


def check_correct_driver():
    """
    check for correct driver version
    install if not found, otherwise do nothing
    """
    smi_output = run_shell_cmd("nvidia-smi --query-gpu=gpu_name --format=csv")
    # 40 series gpus require newer drivers
    # has_40_series = "RTX 40" in smi_output
    # target_version = "525.105.17" if has_40_series else "510.73.05"
    target_version = "535.154.05"
    # check if installed
    nvidia_output = run_shell_cmd(f'cat /proc/driver/nvidia/version | grep "{target_version}"')
    run_shell_cmd("sudo apt-get install mesa-utils -y")
    opengl_output = run_shell_cmd(f'DISPLAY=:0.0 glxinfo | grep "OpenGL version" | grep "NVIDIA {target_version}"')
    if nvidia_output and opengl_output:
        return

    # not installed so uninstall existing and install target
    run_shell_cmd(f"./nvidia_driver_update.sh {target_version} --force")


def _add_swap(desired_swap):
    """
    add desired swap GB of swap to system
    requires that there's at least desired_swap+2 GB of free storage
    """
    DAEMON_LOGGER.info(f"Adding {desired_swap}GB of swap")
    # each batch is 128MB which is 1/8 of a GB
    n_batches = math.ceil(desired_swap * 8)
    run_shell_cmd(f"sudo dd if=/dev/zero of=/swapfile bs=128M count={n_batches}")
    run_shell_cmd("sudo chmod 600 /swapfile")
    run_shell_cmd("sudo mkswap /swapfile")
    run_shell_cmd("sudo swapon /swapfile")
    run_shell_cmd("echo /swapfile swap swap defaults 0 0 >> /etc/fstab")


def check_memory():
    """
    check to make sure system has enough memory and configure swap if not
    """
    command_output = run_shell_cmd("free --giga", format_output=False, quiet=True)
    command_output = command_output.splitlines()
    total_ram = float(command_output[1].split()[1])
    total_swap = float(command_output[2].split()[1])
    # want to have at least this many GB of memory for the more resource intensive renders
    desired_total = 16.0
    must_configure_swap = (total_swap == 0.0) and (total_ram + total_swap < desired_total)
    if must_configure_swap:
        desired_swap = desired_total - total_ram
        drive_info = run_shell_cmd("""df / | grep "/dev" | awk -v N=4 '{print $N}'""", format_output=False, quiet=True)
        # free drive space in GB
        free_drive_size = float(drive_info)/1000000
        # ensure there's at least this many GB in storage left after configuring swap
        min_space_remaining = 3.0
        if free_drive_size < min_space_remaining:
            return
        
        if desired_swap + min_space_remaining > free_drive_size:
            desired_swap = free_drive_size - min_space_remaining
            
        _add_swap(desired_swap)


def get_oc_settings():
    """
    read and return currently-set overclock settings and associated hash from nvidia_oc_conf file
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
    # for when only 1 value is specified for all gpus
    if len(setting) == 1 and n_gpus > 1:
        setting = [setting[0]]*n_gpus
    
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
    gpu_indexes = [int(gpu) for gpu in gpu_indexes]
    original_oc_settings, oc_hash, db = read_oc_settings()
    current_oc_settings, current_oc_hash = get_oc_settings()
    # do nothing if overclocking not set
    if not current_oc_settings:
        # release Overclock table lock
        with app.app_context():
            db.session.commit()
        
        return
    
    new_oc_settings = copy.deepcopy(current_oc_settings)
    # TODO: find n_gpus this way because there might be unsupported gpus present that hive supports? doesn't work when no oc settings set
    # n_gpus = max([len(new_oc_settings[k].split()) for k in new_oc_settings])
    n_gpus = len(gpu_indexes)
    # do nothing if 0 because it means none of the supported gpus are overclocked anyways
    if n_gpus == 0:
        # release Overclock table lock
        with app.app_context():
            db.session.commit()

        return

    is_different = _check_hash_difference(current_oc_settings, oc_hash, current_oc_hash, db)
    if is_different:
        original_oc_settings = current_oc_settings
    # setting values to 0 does a reset to default OC settings
    new_values = ["0"]*len(gpu_indexes)
    _replace_settings(n_gpus, new_oc_settings, gpu_indexes, "CLOCK", new_values)
    _replace_settings(n_gpus, new_oc_settings, gpu_indexes, "MEM", new_values)
    # disable undervolting only for test hosts
    if socket.gethostname() in TEST_HOSTS:
        _replace_settings(n_gpus, new_oc_settings, gpu_indexes, "PLIMIT", new_values)
        
    new_oc_settings["OHGODAPILL_ENABLED"] = ""
    _write_settings(new_oc_settings)
    _, new_oc_hash = get_oc_settings()
    # releases lock
    write_oc_settings(original_oc_settings, new_oc_hash, db)


def enable_oc(gpu_indexes):
    """
    set overclock settings to original oc_settings
    """
    gpu_indexes = [int(gpu) for gpu in gpu_indexes]
    original_oc_settings, oc_hash, db = read_oc_settings()
    current_oc_settings, current_oc_hash = get_oc_settings()
    # do nothing if overclocking not set
    if not current_oc_settings or not original_oc_settings:
        # release Overclock table lock
        with app.app_context():
            db.session.commit()

        return

    is_different = _check_hash_difference(current_oc_settings, oc_hash, current_oc_hash, db)
    # do nothing if user set new oc settings, since we assume these are already enabled
    if is_different:
        # release Overclock table lock
        with app.app_context():
            db.session.commit()

        return
    new_oc_settings = copy.deepcopy(current_oc_settings)
    # TODO: find n_gpus this way because there might be unsupported gpus present that hive supports? doesn't work when no oc settings set
    # n_gpus = max([len(new_oc_settings[k].split()) for k in new_oc_settings])
    n_gpus = len(gpu_indexes)
    original_clock_values = _get_setting_from_key(original_oc_settings, "CLOCK", n_gpus)
    original_clock_values = [original_clock_values[idx] for idx in gpu_indexes]
    original_mem_values = _get_setting_from_key(original_oc_settings, "MEM", n_gpus)
    original_mem_values = [original_mem_values[idx] for idx in gpu_indexes]
    original_pill_value = original_oc_settings["OHGODAPILL_ENABLED"]
    _replace_settings(n_gpus, new_oc_settings, gpu_indexes, "CLOCK", original_clock_values)
    _replace_settings(n_gpus, new_oc_settings, gpu_indexes, "MEM", original_mem_values)
    # modifying undervolting only for test hosts
    if socket.gethostname() in TEST_HOSTS:
        original_plimit_values = _get_setting_from_key(original_oc_settings, "PLIMIT", n_gpus)
        original_plimit_values = [original_plimit_values[idx] for idx in gpu_indexes]
        _replace_settings(n_gpus, new_oc_settings, gpu_indexes, "PLIMIT", original_plimit_values)
    new_oc_settings["OHGODAPILL_ENABLED"] = original_pill_value
    _write_settings(new_oc_settings)
    # original oc settings not overwritten, but we just overwrote oc file so need to update to new hash
    _, new_oc_hash = get_oc_settings()
    # releases lock
    write_oc_settings(original_oc_settings, new_oc_hash, db)


def write_oc_settings(oc_settings, oc_hash, db, commit=True):
    """
    write oc settings and hash to db
    """
    oc_dict = {"oc_settings": oc_settings, "oc_hash": oc_hash}
    oc_str = json.dumps(oc_dict)
    # must use db object to query because Overclock was initialized with different db connection that doesn't have lock
    with app.app_context():
        existing_oc_settings = db.session.query(Overclock).all()
        if existing_oc_settings:
            existing_oc_settings = existing_oc_settings[-1]
            existing_oc_settings.oc_settings = oc_str
        else:
            oc_settings = Overclock(oc_settings=oc_str)
            db.session.add(oc_settings)

        if commit:
            db.session.commit()


def read_oc_settings():
    """
    read oc settings and hash from db
    requires overclock settings to already exist in db
    requires calling function to free the overclock table lock by calling db.session.commit()
    """
    # with_for_update acquires lock on the overclock table, which is necessary to avoid multiple concurrent threads from messing up the settings
    # if a concurrent thread tries to read or write the table when another thread has the lock, it will wait until the lock is released
    with app.app_context():
        existing_oc_settings = db.session.query(Overclock.oc_settings).with_for_update().first()
    existing_oc_settings = existing_oc_settings[0]
    oc_dict = json.loads(existing_oc_settings)

    return oc_dict["oc_settings"], oc_dict["oc_hash"], db


def _check_hash_difference(new_oc_settings, original_oc_hash, new_oc_hash, db):
    """
    check if oc file was modified by another program. If so, we save changes to oc tmp file
    return boolean indicating whether difference found
    """
    if original_oc_hash != new_oc_hash:
        DAEMON_LOGGER.info(f"Detected changes to OC settings: {new_oc_settings}")
        write_oc_settings(new_oc_settings, new_oc_hash, db, commit=False)

        return True

    return False


def install_or_update_benchmark():
    """
    install benchmark software if not already installed
    """
    if os.path.exists("octane/octane"):
        return
    
    DAEMON_LOGGER.info("Installing benchmarking software...")
    run_shell_cmd("wget https://pub-de5d977d4e044ce485eb03586e814764.r2.dev/octane.tar.gz")
    run_shell_cmd("mkdir -p octane && tar -xzf octane.tar.gz -C octane --strip-components 1")


def check_installation():
    """
    check installation for requirements not necessarily installed during first startup
    install anything missing
    """
    check_correct_driver()
    check_memory()
    install_or_update_crypto_miner()
    install_or_update_benchmark()
    run_shell_cmd("sudo apt-get install software-properties-common -y", quiet=True)
    run_shell_cmd("sudo add-apt-repository ppa:deki/firejail -y", quiet=True)
    run_shell_cmd("sudo apt-get install firejail firejail-profiles -y", quiet=True)
    run_shell_cmd('rm octane/started.txt', quiet=True)
    run_shell_cmd('rm octane/benchmark.txt', quiet=True)
    run_shell_cmd("/etc/init.d/mysql start", quiet=True)
    run_shell_cmd("mkdir /var/log/mysql", quiet=True)
    run_shell_cmd("sudo chown -R mysql:mysql /var/log/mysql", quiet=True)
    run_shell_cmd("sudo systemctl start mysql", quiet=True)
    run_shell_cmd('''mysql -u root -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY 'daemon';"''', quiet=True)
    run_shell_cmd('mysql -u root -pdaemon -e "create database daemon;"', quiet=True)
    run_shell_cmd('mysql -u root -pdaemon -e "SET session wait_timeout=10;"', quiet=True)
    run_shell_cmd('mysql -u root -pdaemon -e "SET interactive_timeout=10;"', quiet=True)
    with app.app_context():
        db.drop_all()
        db.create_all()


def install_all_requirements():
    """
    install all rentaflop requirements except specific versioned packages that are checked on startup, such as nvidia driver and crypto miner
    """
    run_shell_cmd("pip3 install -r requirements.txt")
    # skipping system update
    # list of sources for security updates
    # run_shell_cmd("sudo sh -c 'grep ^deb /etc/apt/sources.list | grep security > /etc/apt/sources.security.only.list'")
    # perform system update
    # update({"type": "system"}, reboot=False)
    # make sure we have all available storage for installation
    run_shell_cmd("disk-expand")
    # install dependencies
    run_shell_cmd("sudo apt-get install ca-certificates curl gnupg lsb-release -y")
    run_shell_cmd("sudo apt-get update -y")
    run_shell_cmd("sudo apt-get install mysql-server -y")
    run_shell_cmd("sudo apt-get install python3-pip -y && pip3 install speedtest-cli")
    run_shell_cmd("git config --global --add safe.directory /hive/miners/custom/rentaflop/rentaflop-miner")


def get_render_file(rentaflop_id, job_id):
    """
    fetch render file from rentaflop servers
    return file, filename
    """
    server_url = "https://api.rentaflop.com/host/input"
    data = {"rentaflop_id": str(rentaflop_id), "job_id": str(job_id)}
    api_response = requests.post(server_url, json=data)
    file_url = api_response.json()["url"]
    file_response = requests.get(file_url, stream=True)
    render_file = file_response.content
    # parse out filename from download URL
    # NOTE: if s3 upload dir changes, then this must also change
    filename = file_url.split("https://rentaflop-render-uploads.s3.amazonaws.com/")[1].split("?AWSAccessKeyId=")[0]

    return render_file, filename


def pull_latest_code():
    """
    pull latest rentaflop miner code
    """
    # use test branch develop if testing on rentaflop machine otherwise use prod branch master
    branch = "develop" if socket.gethostname() in TEST_HOSTS else "master"
    run_shell_cmd(f"git checkout {branch}")
    run_shell_cmd("git pull")


def get_last_frame_completed(task_dir, start_frame):
    """
    return last frame number completed, None if 0 frames completed
    """
    log_path = os.path.join(task_dir, "log.txt")
    output = run_shell_cmd(f"tail -100 {log_path} | grep -e 'Fra:'", very_quiet=True, format_output=False)
    if not output:
        return None
    
    lines = output.splitlines()
    frame_in_progress = start_frame
    for line in reversed(lines):
        if line.startswith("Fra:"):
            frame_in_progress = int(line.split()[0].replace("Fra:", ""))
            break

    if frame_in_progress == start_frame:
        return None

    return frame_in_progress - 1


def calculate_frame_times(task_dir, start_frame):
    """
    calculate total time in minutes spent rendering frames, not including preprocessing
    requires started_render.txt to exist plus frames are still present
    return first_frame_time, subsequent_frames_avg
    """
    start_file_path = os.path.join(task_dir, "started_render.txt")
    if not os.path.exists(start_file_path):
        return None, None
    
    output_files = os.path.join(task_dir, "output/*")
    list_of_files = glob.glob(output_files)
    if not list_of_files:
        return None, None

    n_frames = len(list_of_files)
    render_start_time = os.path.getmtime(start_file_path)
    render_start_time = dt.datetime.fromtimestamp(render_start_time)
    # videos will output just one file, such as 0001-0500.mov
    if n_frames == 1:
        _, file_ext = os.path.splitext(list_of_files[0])
        # if it is indeed a video file, we have to use logs to determine frame finish times
        if file_ext.lower() in VIDEO_FORMATS:
            last_frame_completed = get_last_frame_completed(task_dir, start_frame)
            # since it's hard to get exact frame time completion from logs, we assume last frame just finished and set first and subsequent time equal
            if last_frame_completed is not None:
                now = dt.datetime.now()
                n_frames_rendered = last_frame_completed - start_frame + 1
                render_duration = now - render_start_time
                render_time = render_duration.total_seconds()/60.0
                # since we aren't measuring frame time precisely here, we reduce the estimate via a multiplier < 1
                video_estimate_multiplier = 0.67
                subsequent_frames_avg = video_estimate_multiplier * render_time / n_frames_rendered
                first_frame_time = subsequent_frames_avg
                
                return first_frame_time, subsequent_frames_avg
            else:
                return None, None

    first_file = min(list_of_files, key=os.path.getmtime)
    last_file = max(list_of_files, key=os.path.getmtime)
    first_frame_finish = os.path.getmtime(first_file)
    first_frame_finish = dt.datetime.fromtimestamp(first_frame_finish)
    last_frame_finish = os.path.getmtime(last_file)
    last_frame_finish = dt.datetime.fromtimestamp(last_frame_finish)

    first_frame_duration = first_frame_finish-render_start_time
    first_frame_time = first_frame_duration.total_seconds()/60.0
    # handle 1-frame task edge case
    if n_frames == 1:
        subsequent_frames_avg = first_frame_time
    else:
        subsequent_frames_duration = last_frame_finish-first_frame_finish
        subsequent_frames_time = subsequent_frames_duration.total_seconds()/60.0
        subsequent_frames_avg = subsequent_frames_time / (n_frames - 1)

    return first_frame_time, subsequent_frames_avg


def get_rentaflop_id():
    """
    reads registration file and returns rentaflop id
    return rentaflop_id
    """
    rentaflop_config = {}
    with open(REGISTRATION_FILE, "r") as f:
        rentaflop_config = json.load(f)
        
    return rentaflop_config.get("rentaflop_id", "")
