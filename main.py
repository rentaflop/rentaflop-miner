"""
main code for rentaflop miner daemon
"""
import os
import logging
import uuid
import multiprocessing
from flask import jsonify, request, abort, redirect
from flask_apscheduler import APScheduler
from config import DAEMON_LOGGER, FIRST_STARTUP, LOG_FILE, REGISTRATION_FILE, DAEMON_PORT, app, db, _get_logger
from utils import *
from task_queue import push_task, pop_task, update_queue, queue_status
import sys
import requests
from requirement_checks import perform_host_requirement_checks
import json
import socket
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import time
import traceback
import subprocess
from threading import Thread
import datetime as dt
import io
import zipfile


def _start_mining(startup=False):
    """
    starts mining during GPU downtime
    startup will sleep for several seconds before attempting to start mining
    """
    # if just started, wait for gpus to "wake up" on boot
    if startup:
        time.sleep(10)
        DAEMON_LOGGER.debug("Starting crypto miner")

        return mine({"action": "start"})

    state = get_state(RENTAFLOP_CONFIG["available_resources"], queue_status, gpu_only=True, quiet=True)
    status = state["status"]
    gpus_stopped = status == "stopped"
    gpus_stopped_later = gpus_stopped
    # we want to make sure we're not starting miner back up right before a task is about to be run so we try again before restarting
    if gpus_stopped and not startup:
        time.sleep(10)
        state = get_state(RENTAFLOP_CONFIG["available_resources"], queue_status, gpu_only=True, quiet=True)
        status = state["status"]
        gpus_stopped_later = status == "stopped"
    
    if gpus_stopped and gpus_stopped_later:
        DAEMON_LOGGER.debug("Starting crypto miner")
        mine({"action": "start"})


def _get_registration(is_checkin=True):
    """
    return registration details from registration file or register if it doesn't exist
    """
    config_changed = False
    if not is_checkin:
        with open(REGISTRATION_FILE, "r") as f:
            rentaflop_config = json.load(f)
            # we only save things we need to detect changes on in the registration file
            # this is because rentaflop db saves these so we need to know when to update it
            rentaflop_id = rentaflop_config.get("rentaflop_id", "")
            wallet_address = rentaflop_config.get("wallet_address", "")
            task_miner_currency = rentaflop_config.get("task_miner_currency", "")
            daemon_port = rentaflop_config.get("daemon_port", 0)
            email = rentaflop_config.get("email", "")
            sandbox_id = rentaflop_config.get("sandbox_id", "")
            current_email, disable_crypto, current_wallet_address, pool_url, hash_algorithm, password, crypto_miner_config, current_task_miner_currency = \
                get_custom_config()
            if current_email != email and current_email:
                email = current_email
                config_changed = True
            if current_wallet_address != wallet_address and current_wallet_address:
                wallet_address = current_wallet_address
                config_changed = True
            if current_task_miner_currency != task_miner_currency and current_task_miner_currency:
                task_miner_currency = current_task_miner_currency
                config_changed = True
            if daemon_port != DAEMON_PORT:
                config_changed = True
            crypto_config = {"wallet_address": wallet_address, "email": email, "disable_crypto": disable_crypto, "pool_url": pool_url, \
                             "hash_algorithm": hash_algorithm, "pass": password, "crypto_miner_config": crypto_miner_config, "task_miner_currency": task_miner_currency}
    else:
        rentaflop_id, sandbox_id, crypto_config = RENTAFLOP_CONFIG["rentaflop_id"], RENTAFLOP_CONFIG["sandbox_id"], RENTAFLOP_CONFIG["crypto_config"]

    # rentaflop id is either read from the file, already set if it's a checkin, or is initial registration where it's empty str
    is_registered = rentaflop_id != ""
    # sometimes file read appears to fail and we erroneously create a new registration, so this prevents it
    if not is_registered:
        # TODO figure out a better way to do this without reading log file (perhaps server handles it by checking if ip address and devices already registered)
        registrations = run_shell_cmd(f"cat {LOG_FILE} | grep 'Registration successful.'", format_output=False, very_quiet=True)
        registrations = [] if not registrations else registrations.splitlines()
        # we've already registered and logged it, so file was read incorrectly and we should try again
        if len(registrations) > 0:
            DAEMON_LOGGER.error("Rentaflop id not set but found successful registration, retrying...")
            time.sleep(30)
            
            return _get_registration(is_checkin=is_checkin)
    
    # using external website to get ip address
    try:
        ip = requests.get('https://api.ipify.org').content.decode('utf8')
    except requests.exceptions.ConnectionError:
        ip = None
    # register host with rentaflop or perform checkin if already registered
    data = {"state": get_state(RENTAFLOP_CONFIG["available_resources"], queue_status, quiet=is_checkin, version=RENTAFLOP_CONFIG["version"]), \
            "ip": ip, "rentaflop_id": rentaflop_id, "email": crypto_config["email"], "wallet_address": crypto_config["wallet_address"], \
            "task_miner_currency": crypto_config["task_miner_currency"]}
    if not is_checkin:
        data["ignore_instruction"] = True
    response_json = post_to_rentaflop(data, "daemon", quiet=is_checkin)
    if response_json is None:
        type_str = "checkin" if is_checkin else "registration"
        DAEMON_LOGGER.error(f"Failed {type_str}!")
        if is_checkin:
            return {}
        if is_registered:
            return rentaflop_id, sandbox_id, crypto_config
        raise
    elif is_checkin:
        return response_json
    
    if not is_registered:
        rentaflop_id = response_json["rentaflop_id"]
        sandbox_id = response_json["sandbox_id"]
        config_changed = True

    # if we just registered or changed config, save registration info
    if config_changed:
        # still saving daemon port because it's used by h-stats.sh to query status
        update_config(rentaflop_id, DAEMON_PORT, sandbox_id, crypto_config["wallet_address"], crypto_config["email"], crypto_config["task_miner_currency"])
        # don't change this without also changing the grep search for this string above
        if not is_registered:
            DAEMON_LOGGER.debug("Registration successful.")

    return rentaflop_id, sandbox_id, crypto_config


def _handle_checkin():
    """
    handles checkins with rentaflop servers and executes instructions returned
    continues reading instructions until queue is empty
    """
    finished = False
    while not finished:
        # instruction looks like {"cmd": ..., "params": ..., "rentaflop_id": ...}
        # {} if instruction queue empty
        instruction_json = _get_registration()
        # if no instruction, do nothing otherwise execute instruction
        if not instruction_json:
            finished = True
        else:
            # hand off instruction to localhost web server
            files = {"json": json.dumps(instruction_json)}
            requests.post(f"https://localhost:{DAEMON_PORT}", files=files, verify=False)


def _first_startup():
    """
    run rentaflop installation steps
    """
    install_all_requirements()
    check_correct_driver()


def _subsequent_startup():
    """
    handle case where log file already exists and we've had a prior daemon startup
    """
    # if update passed as clarg, then we need to call update again to handle situation when
    # update function itself has been updated in the rentaflop code update
    if len(sys.argv) > 1:
        if sys.argv[1] == "update":
            DAEMON_LOGGER.debug("Entering second update...")
            target_version = "" if len(sys.argv) < 3 else sys.argv[2]
            update({"type": "rentaflop", "target_version": target_version}, second_update=True)
            DAEMON_LOGGER.debug("Exiting second update.")
            # flushing logs and exiting daemon now since it's set to restart in 3 seconds
            logging.shutdown()
            sys.exit(0)
        elif sys.argv[1] == "sleep":
            time.sleep(5)
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
        # ensure anything that started up during update gets killed
        kill_other_daemons()
    elif "Exiting update." not in last_line and "Stopping daemon." not in last_line:
        # error state
        DAEMON_LOGGER.debug("Daemon crashed.")


def _get_available_resources():
    """
    run requirement checks and return dict containing available VM system resources
    """
    passed_checks, resources = perform_host_requirement_checks()
    if not passed_checks:
        print("Failed minimum requirement checks. Please see our minimum system requirements at https://portal.rentaflop.com/blog/hosting")
        DAEMON_LOGGER.error("Failed requirement checks!")
        raise Exception(f"Failed requirement checks! Available GPUS: {resources}")
    
    DAEMON_LOGGER.debug(f"Finished requirement checks, found available resources: {resources}")

    return resources


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
    # clean logs if they're too large
    clean_logs(clear_contents=True)
    # do a code pull in case this is first startup in a long time or if the instruction retrieval breaks
    pull_latest_code()
    os.chdir(os.path.dirname(os.path.realpath(__file__)))
    run_shell_cmd("sudo nvidia-smi -pm 1", quiet=True)
    run_shell_cmd("./nvidia_uvm_init.sh", quiet=True)
    # must do installation check before anything required by it is used
    check_installation()
    global RENTAFLOP_CONFIG
    RENTAFLOP_CONFIG["available_resources"] = _get_available_resources()
    RENTAFLOP_CONFIG["version"] = run_shell_cmd("git rev-parse --short HEAD", quiet=True, format_output=False).replace("\n", "")
    RENTAFLOP_CONFIG["rentaflop_id"], RENTAFLOP_CONFIG["sandbox_id"], RENTAFLOP_CONFIG["crypto_config"] = \
        _get_registration(is_checkin=False)
    # setting env var for task queue to use
    os.environ["SANDBOX_ID"] = RENTAFLOP_CONFIG["sandbox_id"]
    oc_settings, oc_hash = get_oc_settings()
    # db table contains original (set by user in hive) oc settings and hash of current (not necessarily original) oc settings
    write_oc_settings(oc_settings, oc_hash, db)
    DAEMON_LOGGER.debug(f"Found OC settings: {oc_settings}")
    if not RENTAFLOP_CONFIG["crypto_config"]["disable_crypto"]:
        _start_mining(startup=True)


def send_to_task_queue(data):
    """
    send commands, files, and params to job queue functions
    """
    cmd = data.get("cmd")
    params = data.get("params")

    return TASK_QUEUE_CMD_TO_FUNC[cmd](params)


def mine(params):
    """
    handle commands related to mining, whether crypto mining or guest "mining"
    params looks like {"action": "start" | "stop", "task_id": "13245", ...}
    iff render job, we receive task_id parameter (if action is start) that contains data to be rendered
    """
    action = params["action"]
    task_id = params.get("task_id")
    start_frame = params.get("start_frame")
    n_frames = params.get("n_frames")
    job_id = params.get("job_id")
    filename = params.get("filename")
    blender_version = params.get("blender_version")
    render_settings = params.get("render_settings")
    directives = params.get("directives")
    is_cpu = False
    cuda_visible_devices = ""
    if directives:
        directives = directives.split(";")
        for directive in directives:
            k, v = directive.split("=")
            if k == "is_cpu" and v.lower() == "true":
                is_cpu = True
            elif k == "CUDA_VISIBLE_DEVICES":
                cuda_visible_devices = v
    
    gpu_indexes = RENTAFLOP_CONFIG["available_resources"]["gpu_indexes"]
    is_render = False
    if task_id:
        is_render = True
    
    if action == "start":
        if is_render:
            extension = os.path.splitext(filename)[1] if filename else None
            is_zip = True if extension in [".zip"] else False
            file_uuid = filename.split("-")[0] if filename else None
            # inserts dir for file into cache if not already there; returns bool indicating whether inserted or already existed and corresponding dir where file is
            newly_inserted, file_cached_dir = push_cache(file_uuid)
            # if we inserted a new dir, we must actually download file and save it there
            if newly_inserted:
                render_file = get_render_file(RENTAFLOP_CONFIG["rentaflop_id"], job_id)
                if is_zip:
                    # NOTE: partially duplicated in job_queue.py and scan.py
                    with io.BytesIO(render_file) as archive:
                        archive.seek(0)
                        with zipfile.ZipFile(archive, mode='r') as zipf:
                            try:
                                zipf.extractall(file_cached_dir)
                            except NotImplementedError:
                                DAEMON_LOGGER.debug("Compression scheme not supported, attempting CLI extraction...")
                                zip_path = os.path.join(file_cached_dir, "render_file.zip")
                                with open(zip_path, "wb") as f:
                                    f.write(render_file)
                                subprocess.check_output(f"unzip {zip_path} -d {file_cached_dir}", shell=True, encoding="utf8", stderr=subprocess.STDOUT)
                else:
                    with open(os.path.join(file_cached_dir, "render_file.blend"), "wb") as f:
                        f.write(render_file)
                
            stop_crypto_miner()
            disable_oc(gpu_indexes)
            end_frame = start_frame + n_frames - 1
            data = {"cmd": "push_task", "params": {"task_id": task_id, "start_frame": start_frame, "end_frame": end_frame, "blender_version": blender_version, \
                                                   "is_cpu": is_cpu, "cuda_visible_devices": cuda_visible_devices, "render_settings": render_settings, \
                                                   "file_cached_dir": file_cached_dir, "is_render": is_render}}
            send_to_task_queue(data)
        else:
            if RENTAFLOP_CONFIG["crypto_config"]["disable_crypto"]:
                return
            _stop_all()
            # 4059 is default port from hive
            crypto_port = 4059
            hostname = socket.gethostname()
            enable_oc(gpu_indexes)
            # does nothing if already mining
            start_crypto_miner(crypto_port, hostname, RENTAFLOP_CONFIG["crypto_config"])
    elif action == "stop":
        if is_render:
            data = {"cmd": "pop_task", "params": {"task_id": task_id}}
            send_to_task_queue(data)
        else:
            stop_crypto_miner()


def _stop_all():
    """
    stop all tasks and crypto mining processes
    """
    DAEMON_LOGGER.debug("Stopping tasks...")
    # stops all tasks and benchmarking
    data = {"cmd": "queue_status", "params": {}}
    result = send_to_task_queue(data)
    for task_id in result["queue"]:
        data = {"cmd": "pop_task", "params": {"task_id": task_id}}
        send_to_task_queue(data)
    
    run_shell_cmd('pkill -f t-rex')
    run_shell_cmd('pkill -f octane')
    run_shell_cmd('pkill -f blender')
    DAEMON_LOGGER.debug("Tasks stopped.")
            
            
def update(params, reboot=True, second_update=False):
    """
    handle commands related to rentaflop software and system updates
    params looks like {"type": "rentaflop" | "system", "target_version": "abc123"}
    target_version is git version to update to when type is rentaflop; if not set, we update to latest master
    reboot controls whether system update will reboot
    second_update is set to True to indicate current update code running is already up to date,
    False if it hasn't been updated yet
    if second_update is True, we're performing the real update, as latest code for this function
    is currently running, whereas on the first update this function may not have been up to date
    """
    update_type = params["type"]
    if update_type == "rentaflop":
        # must run all commands even if second update
        target_version = params.get("target_version", "")
        pull_latest_code()
        if target_version:
            run_shell_cmd(f"git checkout {target_version}")
        # ensure everything stopped so we can run new stuff with latest code
        _stop_all()
        update_param = "" if second_update else f" update {target_version}"
        # ensure a daemon is still running during an update; prevents hive from trying to restart it itself
        subprocess.Popen(["python3", "daemon.py", "sleep"])
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
    _stop_all()
    # clean up rentaflop host software
    daemon_py = os.path.realpath(__file__)
    rentaflop_miner_dir = os.path.dirname(daemon_py)
    run_shell_cmd(f"rm -rf {rentaflop_miner_dir}", quiet=True)

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
    return {"state": get_state(RENTAFLOP_CONFIG["available_resources"], queue_status, quiet=True, \
                               version=RENTAFLOP_CONFIG["version"], algo=RENTAFLOP_CONFIG["crypto_config"].get("hash_algorithm"))}


def benchmark(params):
    """
    run performance benchmark for gpus
    """
    stop_crypto_miner()
    gpu_indexes = RENTAFLOP_CONFIG["available_resources"]["gpu_indexes"]
    disable_oc(gpu_indexes)
    data = {"cmd": "push_task", "params": {"task_id": -1}}
    send_to_task_queue(data)


def prep_daemon_shutdown(server):
    """
    prepare daemon for shutdown without assuming system is restarting
    stops all mining jobs and terminates server
    """
    _stop_all()
    gpu_indexes = RENTAFLOP_CONFIG["available_resources"]["gpu_indexes"]
    gpu_indexes = [int(gpu) for gpu in gpu_indexes]
    # make sure we restore oc settings back to original
    enable_oc(gpu_indexes)
    DAEMON_LOGGER.debug("Stopping server...")
    time.sleep(5)
    if server:
        server.terminate()
    DAEMON_LOGGER.debug("Stopping daemon.")
    logging.shutdown()


def clean_logs(clear_contents=True, error=None):
    """
    send logs to rentaflop servers and clear contents of logs, leaving an 1-line file indicating registration
    """
    logs = send_logs({})
    if error:
        logs["error"] = error
    if RENTAFLOP_CONFIG["rentaflop_id"]:
        logs["rentaflop_id"] = RENTAFLOP_CONFIG["rentaflop_id"]
    # if we're not clearing contents then we assume we're sending back to raf servers
    if not clear_contents:
        post_to_rentaflop(logs, "logs", quiet=True)
    # clear contents if flag set and log file is over 100 MB
    if clear_contents and os.path.getsize(LOG_FILE) > 100000000:
        with open(LOG_FILE, "w") as f:
            # must write this because of check in _get_registration
            f.write("Registration successful.")

        # daemon logger is corrupted after cleaning so we restart daemon and might as well do an update
        update({"type": "rentaflop"})
        time.sleep(5)
        sys.exit(0)


@app.before_request
def before_request():
    # don't allow anyone who isn't rentaflop to communicate with host daemon
    # only people who know a host's rentaflop id are the host and rentaflop
    # file size check in app config (render files downloaded separately and not sent to this web server)
    json_file = request.files.get("json")
    request_json = json.loads(json_file.read())
    json_file.seek(0)
    request_rentaflop_id = request_json.get("rentaflop_id", "")
    if request_rentaflop_id != RENTAFLOP_CONFIG["rentaflop_id"]:
        return abort(403)
    
    # force https
    if not request.is_secure:
        url = request.url.replace('http://', 'https://', 1)
        code = 301

        return redirect(url, code=code)


def run_flask_server(q):
    @app.route("/", methods=["POST"])
    def index():
        request_json = json.loads(request.files.get("json").read())
        cmd = request_json.get("cmd")
        params = request_json.get("params")
        
        func = CMD_TO_FUNC.get(cmd)
        finished = False
        if func:
            try:
                if cmd != "status":
                    func_log = log_before_after(func, params)
                    finished = func_log()
                else:
                    # avoid logging on status since this is called every 10 seconds by hive stats checker
                    finished = func(params)
            except Exception as e:
                DAEMON_LOGGER.exception(f"Caught exception: {e}")
                error = traceback.format_exc()
                DAEMON_LOGGER.error(f"More info on exception: {error}")
        if finished is True:
            q.put(finished)
        # finished isn't True but it's not Falsey, so return it in response
        if (finished is not True) and finished:
            return jsonify(finished), 200

        return jsonify("200")
    
    app.run(host='0.0.0.0', port=DAEMON_PORT, ssl_context='adhoc')
    
    
CMD_TO_FUNC = {
    "mine": mine,
    "update": update,
    "uninstall": uninstall,
    "send_logs": send_logs,
    "status": status,
    "benchmark": benchmark
}
TASK_QUEUE_CMD_TO_FUNC = {
    "push_task": push_task,
    "pop_task": pop_task,
    "update_queue": update_queue,
    "queue_status": queue_status
}
# rentaflop config looks like {"rentaflop_id": ..., "sandbox_id": ..., \
# "available_resources": {"gpu_indexes": [...], "gpu_names": [...]}, "crypto_config": {"wallet_address": ..., \
# "email": ..., "disable_crypto": ..., "pool_url": ..., "hash_algorithm": ..., "pass": ...}, "version": ...}
RENTAFLOP_CONFIG = {"rentaflop_id": None, "sandbox_id": None, "available_resources": {}, \
                    "crypto_config": {}, "version": None}


def main():
    try:
        server = None
        _handle_startup()
        app.secret_key = uuid.uuid4().hex
        # create a scheduler that periodically checks for stopped GPUs and starts mining on them; periodic checkin to rentaflop servers
        scheduler = APScheduler()
        first_run_time = dt.datetime.now() + dt.timedelta(seconds=5)
        if not RENTAFLOP_CONFIG["crypto_config"]["disable_crypto"]:
            scheduler.add_job(id='Start Miners', func=_start_mining, trigger="interval", seconds=60, max_instances=1, next_run_time=first_run_time)
        scheduler.add_job(id='Rentaflop Checkin', func=_handle_checkin, trigger="interval", seconds=60, max_instances=1, next_run_time=first_run_time)
        scheduler.add_job(id='Handle Finished Tasks', func=update_queue, trigger="interval", seconds=10, max_instances=1)
        scheduler.start()
        # run server, allowing it to shut itself down
        q = multiprocessing.Queue()
        server = multiprocessing.Process(target=run_flask_server, args=(q,))
        DAEMON_LOGGER.debug("Starting server...")
        server.start()
        finished = q.get(block=True)
        if finished:
            DAEMON_LOGGER.info("Daemon shutting down for update...")
            prep_daemon_shutdown(server)
    except KeyboardInterrupt:
        DAEMON_LOGGER.info("Daemon stopped by Hive...")
        prep_daemon_shutdown(server)
    except SystemExit:
        # ignoring intentional system exits and allowing daemon to shut itself down
        pass
    except:
        error = traceback.format_exc()
        DAEMON_LOGGER.error(f"Entering update loop because of uncaught exception: {error}")
        # send logs and error data to rentaflop servers
        clean_logs(clear_contents=False, error=error)
        # ensure all requirements are installed in case something broke during first run or an update with new requirements
        install_all_requirements()
        # don't loop too fast
        time.sleep(180)
        # handle runtime errors and other issues by performing an update, preventing most bugs from breaking a rentaflop installation
        update({"type": "rentaflop"})
