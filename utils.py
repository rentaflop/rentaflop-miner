"""
utility functions to be used in various parts of host software
"""
import subprocess
from config import DAEMON_LOGGER, REGISTRATION_FILE, get_app_db
import time
import json
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import os
import tempfile
import copy
import socket


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
CRYPTO_STATS = {"total_khs": "0.0"}

_, DB = get_app_db()
class Overclock(DB.Model):
    id = DB.Column(DB.Integer, primary_key=True)
    # looks like '{"oc_settings": ..., "oc_hash": ...}'
    oc_settings = DB.Column(DB.String(2048))

    def __repr__(self):
        return f"<Overclock {self.oc_settings}>"


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


def get_state(available_resources, gpu_only=False, quiet=False, version=None, algo=None):
    """
    returns a dictionary with all relevant daemon state information
    this includes gpus, running containers, container use, etc.
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
        if float(stats["total_khs"]) > float(CRYPTO_STATS["total_khs"]):
            CRYPTO_STATS = stats
            
    benchmark_container = run_shell_cmd('docker ps --filter "name=rentaflop-benchmark" --filter "ancestor=rentaflop/sandbox" --format {{.Names}}',
                               quiet=quiet, format_output=False).split()
    if benchmark_container:
        # treat benchmark jobs as gpc
        state["status"] = "gpc"
    
    # get all sandbox container names
    container_name = "rentaflop-sandbox"
    sandbox_container = run_shell_cmd(f'docker ps --filter "name={container_name}" --filter "ancestor=rentaflop/sandbox" --format {{.Names}}',
                               quiet=quiet, format_output=False).split()
    if sandbox_container:
        # request queued tasks from docker
        container_ip = run_shell_cmd("docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "+container_name, format_output=False, quiet=quiet).strip()
        url = f"https://{container_ip}"
        data = {"cmd": "status", "params": {}}
        files = {'json': json.dumps(data)}
        result = post_to_sandbox(url, files, quiet=True, very_quiet=True)
        if not result:
            result = {"queue": []}

        container_queue = result.get("queue")
        uptime = result.get("uptime")
        container_state = "stopped"
        # check for existing queue items or give container a couple minutes to start
        if container_queue or (uptime is not None and uptime < 120):
            container_state = "gpc"

        state["status"] = container_state
        state["queue"] = container_queue

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
    # parse custom config args from flight sheet
    # eliminate all whitespace
    custom_user_config = "".join(custom_user_config.split())
    custom_values = custom_user_config.split(";")
    for custom_value in custom_values:
        if custom_value.startswith("EMAIL="):
            email = custom_value.replace("EMAIL=", "")
        elif custom_value.startswith("DISABLE_CRYPTO") and "false" not in custom_value.lower():
            disable_crypto = True

    return email, disable_crypto, wallet_address, pool_url, hash_algorithm, custom_pass


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


def post_to_sandbox(sandbox_url, data, quiet=False, very_quiet=False, timeout=None):
    """
    make post request to docker sandbox servers; do retries since container may have just been started
    catch exceptions resulting from request
    """
    if not quiet:
        DAEMON_LOGGER.debug(f"Sent to sandbox {sandbox_url}: {data}")
    retries = 3
    if not timeout:
        timeout = 3
    for _ in range(retries+1):
        try:
            response = requests.post(sandbox_url, files=data, verify=False)
            response_json = response.json()
            if response_json:
                break
        except (requests.exceptions.ConnectionError, requests.exceptions.InvalidURL, json.decoder.JSONDecodeError,
                ConnectionResetError) as e:
            if not very_quiet:
                DAEMON_LOGGER.error(f"Exception during post request: {e}")
            response_json = {}
            # must return file to beginning of stream if post failed
            if "render_file" in data:
                data["render_file"].seek(0)
            time.sleep(timeout)

    if not quiet:
        DAEMON_LOGGER.debug(f"Received from sandbox {sandbox_url}: {response_json}")

    return response_json


def update_config(rentaflop_id=None, daemon_port=None, sandbox_id=None, wallet_address=None, email=None):
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

    if is_changed:
        with open(REGISTRATION_FILE, "w") as f:
            f.write(json.dumps(rentaflop_config, indent=4, sort_keys=True))


def install_or_update_crypto_miner():
    """
    check for crypto miner installation and install if not found
    update version if installed and not up to date; does nothing if installed and up to date
    """
    target_version = "0.26.5"
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


def stop_crypto_miner():
    """
    stop crypto miner
    """
    # don't use signal 9 to kill because it causes GPU errors (defaults to signal 15)
    run_shell_cmd('killall t-rex')


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
        pools["user"] = crypto_config["wallet_address"]
        pools["url"] = crypto_config["pool_url"]
        pools["pass"] = crypto_config["pass"]
        pools["worker"] = hostname
        config_json["algo"] = crypto_config["hash_algorithm"]

    with open(config_file, "w") as f:
        json.dump(config_json, f)

    # run miner
    os.system(f"./trex/t-rex -c {config_file} --api-bind-http 127.0.0.1:{crypto_port} &")

    # clean up tmp file after 60 seconds without hangup
    run_shell_cmd(f'echo "sleep 60; rm {config_file}" | at now', quiet=True)


def check_correct_driver(reboot=True):
    """
    check for correct driver version
    install if not found, otherwise do nothing
    reboot is required after changing drivers; reboot option toggles this action within this function
    """
    target_version = "510.73.05"
    # check if installed
    output = run_shell_cmd(f'cat /proc/driver/nvidia/version | grep "{target_version}"')
    if output:
        return

    # not installed so uninstall existing and install target
    run_shell_cmd(f"nvidia-driver-update {target_version}")
    if reboot:
        run_shell_cmd("sudo reboot")


def wait_for_sandbox_server(container_ip):
    """
    wait up to 90 seconds for sandbox server to start
    if still not up after 90 seconds, return and hope for the best
    """
    sandbox_url = f"https://{container_ip}/health"
    tries = 9
    timeout = 10
    for _ in range(tries):
        try:
            response = requests.get(sandbox_url, verify=False)
            if response.status_code == 200:
                return
        except (requests.exceptions.ConnectionError, json.decoder.JSONDecodeError) as e:
            pass

        time.sleep(timeout)


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
        db.session.commit()
        
        return
    
    new_oc_settings = copy.deepcopy(current_oc_settings)
    # find n_gpus this way because there might be unsupported gpus present that hive supports
    n_gpus = max([len(new_oc_settings[k].split()) for k in new_oc_settings])
    # do nothing if 0 because it means none of the supported gpus are overclocked anyways
    if n_gpus == 0:
        # release Overclock table lock
        db.session.commit()

        return

    is_different = _check_hash_difference(current_oc_settings, oc_hash, current_oc_hash, db)
    if is_different:
        original_oc_settings = current_oc_settings
    # setting values to 0 does a reset to default OC settings
    new_values = ["0"]*len(gpu_indexes)
    _replace_settings(n_gpus, new_oc_settings, gpu_indexes, "CLOCK", new_values)
    _replace_settings(n_gpus, new_oc_settings, gpu_indexes, "MEM", new_values)
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
        db.session.commit()

        return

    is_different = _check_hash_difference(current_oc_settings, oc_hash, current_oc_hash, db)
    # do nothing if user set new oc settings, since we assume these are already enabled
    if is_different:
        # release Overclock table lock
        db.session.commit()

        return
    new_oc_settings = copy.deepcopy(current_oc_settings)
    # find n_gpus this way because there might be unsupported gpus present that hive supports
    n_gpus = max([len(new_oc_settings[k].split()) for k in new_oc_settings])
    original_clock_values = _get_setting_from_key(original_oc_settings, "CLOCK", n_gpus)
    original_clock_values = [original_clock_values[idx] for idx in gpu_indexes]
    original_mem_values = _get_setting_from_key(original_oc_settings, "MEM", n_gpus)
    original_mem_values = [original_mem_values[idx] for idx in gpu_indexes]
    original_pill_value = original_oc_settings["OHGODAPILL_ENABLED"]
    _replace_settings(n_gpus, new_oc_settings, gpu_indexes, "CLOCK", original_clock_values)
    _replace_settings(n_gpus, new_oc_settings, gpu_indexes, "MEM", original_mem_values)
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
    _, db = get_app_db()
    # with_for_update acquires lock on the overclock table, which is necessary to avoid multiple concurrent threads from messing up the settings
    # if a concurrent thread tries to read or write the table when another thread has the lock, it will wait until the lock is released
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


def check_installation():
    """
    check installation for requirements not necessarily installed during first startup
    install anything missing
    """
    check_correct_driver()
    install_or_update_crypto_miner()
    run_shell_cmd("/etc/init.d/mysql start", quiet=True)
    run_shell_cmd("mkdir /var/log/mysql", quiet=True)
    run_shell_cmd("sudo chown -R mysql:mysql /var/log/mysql", quiet=True)
    run_shell_cmd("sudo systemctl start mysql", quiet=True)
    run_shell_cmd('''mysql -u root -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY 'daemon';"''', quiet=True)
    run_shell_cmd('mysql -u root -pdaemon -e "create database daemon;"', quiet=True)
    app, _ = get_app_db()
    # use db object tables initialized with
    DB.init_app(app)
    DB.drop_all(app=app)
    DB.create_all(app=app)


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
    run_shell_cmd("curl -fsSL https://download.docker.com/linux/debian/gpg \
    | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg --batch --yes")
    run_shell_cmd('echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] \
    https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null')
    run_shell_cmd("distribution=$(. /etc/os-release; echo $ID$VERSION_ID) \
    && curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add - \
    && curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | sudo tee /etc/apt/sources.list.d/nvidia-docker.list")
    run_shell_cmd("sudo apt-get update -y")
    run_shell_cmd("sudo apt-get install docker-ce docker-ce-cli containerd.io nvidia-docker2 -y")
    # docker setup
    run_shell_cmd("sudo sed -i 's/#no-cgroups = false/no-cgroups = true/' /etc/nvidia-container-runtime/config.toml")
    run_shell_cmd(r'''sudo sed -i '$s/}/,\n"userns-remap":"default"}/' /etc/docker/daemon.json''')
    run_shell_cmd("sudo systemctl restart docker")
    run_shell_cmd("echo iptables-persistent iptables-persistent/autosave_v4 boolean true | sudo debconf-set-selections")
    run_shell_cmd("echo iptables-persistent iptables-persistent/autosave_v6 boolean true | sudo debconf-set-selections")
    run_shell_cmd("sudo apt-get install iptables-persistent mysql-server -y")
    run_shell_cmd("sudo apt-get install python3-pip -y && pip3 install speedtest-cli")
    run_shell_cmd("sudo docker build -f Dockerfile -t rentaflop/sandbox .")


def get_render_file(rentaflop_id, job_id):
    """
    fetch render file from rentaflop servers
    """
    server_url = "https://api.rentaflop.com/host/input"
    data = {"rentaflop_id": str(rentaflop_id), "job_id": str(job_id)}
    api_response = requests.post(server_url, json=data)
    file_url = api_response.json()["url"]
    file_response = requests.get(file_url, stream=True)
    render_file = file_response.raw

    return render_file


def pull_latest_code():
    """
    pull latest rentaflop miner code
    """
    # use test branch develop if testing on rentaflop_one otherwise use prod branch master
    branch = "develop" if socket.gethostname() in ["rentaflop_one", "rentaflop_two"] else "master"
    run_shell_cmd(f"git checkout {branch}")
    run_shell_cmd("git pull")
    
