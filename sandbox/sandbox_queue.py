"""
runs listener within docker sandbox and queues compute tasks
mines crypto whenever queue is empty
"""
import subprocess
import multiprocessing
from flask import Flask, jsonify, request
from flask_apscheduler import APScheduler
import os
import datetime as dt
import requests
import json
import uuid
import logging
from flask_sqlalchemy import SQLAlchemy
import pymysql
import sys
import time


def _get_logger(log_file):
    """
    modules use this to create/retrieve and configure how logging works for their specific module
    """
    module_logger = logging.getLogger("sandbox.log")
    handler = logging.FileHandler(log_file)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(filename)s:%(lineno)d %(levelname)s %(asctime)s - %(message)s"))
    module_logger.addHandler(handler)
    module_logger.setLevel(logging.DEBUG)

    return module_logger


def run_shell_cmd(cmd, quiet=False, very_quiet=False):
    """
    if quiet will only print errors, if very_quiet will silence everything including errors
    run cmd and log output
    """
    if very_quiet:
        quiet = True
    output = None
    if not quiet:
        SANDBOX_LOGGER.debug(f'''Running command {cmd}...''')
    try:
        output = subprocess.check_output(cmd, shell=True, encoding="utf8", stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        # print errors unless very quiet
        if not very_quiet:
            SANDBOX_LOGGER.error(f"Exception: {e}\n{e.output}")
    if output and not quiet:
        SANDBOX_LOGGER.debug(f'''Output for {cmd}: {output}''')

    return output


def log_before_after(func, params):
    """
    wrapper to log debug info before and after each daemon command
    """
    def wrapper():
        SANDBOX_LOGGER.debug(f"Entering {func.__name__} with params {params}...")
        ret_val = func(params)
        SANDBOX_LOGGER.debug(f"Exiting {func.__name__}.")

        return ret_val

    return wrapper


def start_mining():
    """
    begin mining crypto, but only if not already mining
    """
    output = run_shell_cmd("pgrep t-rex", very_quiet=True)
    # if already running trex we do nothing, otherwise start miner
    if not output:
        # start with os.system since this needs to be run in background
        os.system("cd trex && ./t-rex -c config.json &")


def stop_mining():
    """
    stop crypto task
    """
    run_shell_cmd("pkill -f 't-rex'")


def push_task(params):
    """
    add a task to the queue
    """
    render_file = params["render_file"]
    task_id = params["task_id"]
    start_frame = params["start_frame"]
    end_frame = params["end_frame"]
    # create directory for task and write render file there
    task_dir = os.path.join(FILE_DIR, task_id)
    os.makedirs(task_dir)
    render_file.save(f"{task_dir}/render_file.blend")
    
    # append task to queue first to prevent mining from starting after the stop call
    task = Task(task_dir=task_dir, task_id=task_id, tsp_id=-1)
    db.session.add(task)
    db.session.commit()
    # make sure mining is stopped before running render task
    stop_mining()
    tsp_id = run_shell_cmd(f"tsp python3 run.py {task_dir} {start_frame} {end_frame}").strip()
    task.tsp_id = tsp_id
    db.session.commit()


def _return_task_with_id(task_id):
    """
    find and return task from queue with task id matching task_id
    return None if not found
    """    
    return Task.query.filter_by(task_id=task_id).first()


def _delete_task_with_id(task_id):
    """
    delete task from db if it exists
    return tsp_id iff deleted, None otherwise
    """
    # not using ORM because this is run in separate thread where app/db are not defined
    conn = pymysql.connect(host='localhost', user='root', password = "sandbox", db='sandbox')
    cur = conn.cursor()
    cur.execute(f"SELECT tsp_id FROM task WHERE task_id='{task_id}';")
    task = cur.fetchone()
    if task:
        task = str(task[0])
        cur.execute(f"DELETE FROM task WHERE task_id='{task_id}';")
        conn.commit()
    
    conn.close()

    return task


def pop_task(params):
    """
    remove task from queue
    does nothing if already removed from queue
    """
    task_id = params["task_id"]
    queued_task = _return_task_with_id(task_id)
    # task already removed
    if not queued_task:
        return
    tsp_id = queued_task.tsp_id
    # remove relevant task from queue
    db.session.delete(queued_task)
    db.session.commit()
    pid = run_shell_cmd(f"tsp -p {tsp_id}")
    if pid is not None:
        pid = pid.strip()
        # kills python process that's running blender plus all its children 
        run_shell_cmd(f"kill $(ps -s {pid} -o pid=)")
    run_shell_cmd(f"tsp -r {tsp_id}")
    task_dir = os.path.join(FILE_DIR, task_id)
    run_shell_cmd(f"rm -rf {task_dir}")


def status(params):
    """
    return contents of queue
    params is empty dict
    """
    tasks = Task.query.all()
    tasks = [task.task_id for task in tasks]
    # h-stats.sh queries trex for mining stats, so we only run it when trex is running
    if not tasks:
        khs_stats = run_shell_cmd("./h-stats.sh", quiet=True)
        if khs_stats:
            khs_stats = khs_stats.splitlines()
        if len(khs_stats) == 2:
            global KHS
            global STATS
            KHS = float(khs_stats[0])
            STATS = json.loads(khs_stats[1])
    # TODO if running gpc, apply rentaflop multiplier to estimate additional crypto earnings
    
    return {"queue": tasks, "khs": KHS, "stats": STATS}


def _send_results(task_id):
    """
    send render results to servers, removing files and queue entry
    """
    task_dir = os.path.join(FILE_DIR, task_id)
    tgz_path = os.path.join(task_dir, "output.tar.gz")
    output = os.path.join(task_dir, "output")
    # zip and send output dir
    run_shell_cmd(f"tar -czf {tgz_path} {output}")
    sandbox_id = os.getenv("SANDBOX_ID")
    server_url = "https://portal.rentaflop.com/api/host/output"
    data = {"task_id": str(task_id), "sandbox_id": str(sandbox_id)}
    files = {'render_file': open(tgz_path, 'rb'), 'json': json.dumps(data)}
    requests.post(server_url, files=files)
    run_shell_cmd(f"rm -rf {task_dir}")
    _delete_task_with_id(task_id)


def handle_finished_tasks():
    """
    checks for any finished tasks and sends results back to servers
    cleans up and removes files afterwards
    starts crypto miner if all tasks are finished
    """
    # task ids in existence on the file system
    task_ids = os.listdir(FILE_DIR)
    for task_id in task_ids:
        # find finished tasks
        if os.path.exists(os.path.join(FILE_DIR, task_id, "finished.txt")):
            # send results, clean files, and remove task from queue
            _send_results(task_id)
            continue
        # set timeout on queued task and kill if exceeded time limit
        start_time = os.path.getmtime(os.path.join(FILE_DIR, task_id, "started.txt"))
        start_time = dt.datetime.fromtimestamp(start_time)
        current_time = dt.datetime.utcnow()
        timeout = dt.timedelta(hours=1)
        if timeout < (current_time-start_time):
            # remove task from queue
            deleted_tsp = _delete_task_with_id(task_id)
            if deleted_tsp is not None:
                pid = run_shell_cmd(f"tsp -p {deleted_tsp}")
                if pid is not None:
                    pid = pid.strip()
                    run_shell_cmd(f"kill -9 {pid}")
            # clean up files
            run_shell_cmd(f"rm -rf {task_dir}")

    # remove finished tasks from tsp queue
    run_shell_cmd("tsp -C", quiet=True)
    # not using ORM because this is run in separate thread where app/db are not defined
    conn = pymysql.connect(host='localhost', user='root', password = "sandbox", db='sandbox')
    cur = conn.cursor()
    n_tasks = cur.execute("SELECT * FROM task;")
    conn.close()
    if n_tasks == 0:
        # nothing left running in queue, so we mine crypto again
        start_mining()


def monitor_mining():
    """
    monitor crypto mining process and optimize it to improve hash rate
    """
    is_miner_running = run_shell_cmd("pgrep t-rex", very_quiet=True)
    if not is_miner_running:
        return
    
    time_of_last_lhr_lock = run_shell_cmd('grep -e "min since last lock. Unlocking ..." /var/log/miner/t-rex/t-rex.log | grep -Po "[0-9]* [0-9]{2}:[0-9]{2}:[0-9]{2}"', quiet=True)
    if not time_of_last_lhr_lock:
        return
    time_of_last_lhr_lock = dt.datetime.strptime(time_of_last_lhr_lock.splitlines()[-1], "%Y%m%d %H:%M:%S")
    # we use system time instead of utc time since log file uses system time
    current_time = dt.datetime.now()
    timeframe = dt.timedelta(minutes=1)
    # lhr lock happened in the last minute, so we restart miner at low lhr tune value
    if (current_time-time_of_last_lhr_lock) < timeframe:
        stop_mining()
        os.system("cd trex && ./t-rex -c config.json --lhr-tune 60 &")


def run_flask_server(q):
    @app.route("/", methods=["POST"])
    def index():
        request_json = json.loads(request.files.get("json").read())
        cmd = request_json.get("cmd")
        params = request_json.get("params")
        render_file = request.files.get("render_file")
        if render_file:
            params["render_file"] = render_file

        to_return = None
        func = CMD_TO_FUNC.get(cmd)
        if func:
            try:
                if cmd != "status":
                    to_return = log_before_after(func, params)()
                else:
                    # avoid logging on status since this is called every 10 seconds by hive stats checker
                    to_return = func(params)
            except Exception as e:
                SANDBOX_LOGGER.exception(f"Caught exception: {e}")
        if to_return is not None:
            return to_return

        return jsonify("200")
    
    app.run(host='0.0.0.0', port=443, ssl_context='adhoc')


CMD_TO_FUNC = {
    "push": push_task,
    "pop": pop_task,
    "status": status,
}
FILE_DIR = "/root/tasks"
os.makedirs(FILE_DIR, exist_ok=True)
LOG_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "sandbox.log")
SANDBOX_LOGGER = _get_logger(LOG_FILE)
KHS=0
STATS="null"


class Config(object):
    SQLALCHEMY_DATABASE_URI = "mysql+pymysql://root:sandbox@localhost/sandbox"
    SECRET_KEY = uuid.uuid4().hex
    SQLALCHEMY_TRACK_MODIFICATIONS = False

os.system("/etc/init.d/mysql start")
os.system('''mysql -u root -e "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY 'sandbox';"''')
os.system('mysql -u root -psandbox -e "create database sandbox;"')

app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer)
    tsp_id = db.Column(db.Integer)
    task_dir = db.Column(db.String(64))

    def __repr__(self):
        return f"<Task {self.task_id} {self.tsp_id} {self.task_dir}>"

db.create_all(app=app)


def main():
    start_mining()
    # if we're running scheduler, don't run server; we do this in separate process because scheduler doesn't run properly when run with server
    if len(sys.argv) == 2 and sys.argv[1] == "scheduler":
        # create a scheduler that periodically checks/handles finished tasks starts mining when there are no tasks in queue
        scheduler = APScheduler()
        scheduler.add_job(id='Handle Finished Tasks', func=handle_finished_tasks, trigger="interval", seconds=10)
        scheduler.add_job(id='Monitor Mining', func=monitor_mining, trigger="interval", seconds=180)
        scheduler.start()
        while True:
            time.sleep(30)

    os.system("python3 sandbox_queue.py scheduler &")
    q = multiprocessing.Queue()
    server = multiprocessing.Process(target=run_flask_server, args=(q,))
    server.start()


if __name__=="__main__":
    main()
