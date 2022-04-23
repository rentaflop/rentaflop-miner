"""
runs listener within docker sandbox and queues compute jobs
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
    output = run_shell_cmd("pgrep t-rex", quiet=True)
    # if already running trex we do nothing, otherwise start miner
    if not output:
        # start with os.system since this needs to be run in background
        os.system("cd trex && ./t-rex -c config.json --no-watchdog &")


def stop_mining():
    """
    stop crypto job
    """
    run_shell_cmd("pkill -f 't-rex'")


def push_job(params):
    """
    add a job to the queue
    """
    render_file = params["render_file"]
    job_id = params["job_id"]
    # create directory for job and write render file there
    job_dir = os.path.join(FILE_DIR, job_id)
    os.makedirs(job_dir)
    render_file.save(f"{job_dir}/render_file.blend")
    
    # append job to queue first to prevent mining from starting after the stop call
    job = Job(job_dir=job_dir, job_id=job_id, tsp_id=-1)
    db.session.add(job)
    db.session.commit()
    # make sure mining is stopped before running render job
    stop_mining()
    tsp_id = run_shell_cmd(f"tsp python3 run.py {job_dir}").strip()
    job.tsp_id = tsp_id
    db.session.commit()


def _return_job_with_id(job_id):
    """
    find and return job from queue with job id matching job_id
    return None if not found
    """    
    return Job.query.filter_by(job_id=job_id).first()


def _delete_job_with_id(job_id):
    """
    delete job from db if it exists
    return tsp_id iff deleted, None otherwise
    """
    # not using ORM because this is run in separate thread where app/db are not defined
    conn = pymysql.connect(host='localhost', user='root', password = "sandbox", db='sandbox')
    cur = conn.cursor()
    cur.execute(f"SELECT tsp_id FROM job WHERE job_id='{job_id}';")
    job = cur.fetchone()
    if job:
        job = str(job[0])
        cur.execute(f"DELETE FROM job WHERE job_id='{job_id}';")
        conn.commit()
    
    conn.close()

    return job


def pop_job(params):
    """
    remove job from queue
    does nothing if already removed from queue
    """
    job_id = params["job_id"]
    queued_job = _return_job_with_id(job_id)
    # job already removed
    if not queued_job:
        return
    tsp_id = queued_job.tsp_id
    # remove relevant job from queue
    db.session.delete(queued_job)
    db.session.commit()
    pid = run_shell_cmd(f"tsp -p {tsp_id}")
    if pid is not None:
        pid = pid.strip()
        # kills python process that's running blender plus all its children 
        run_shell_cmd(f"kill $(ps -s {pid} -o pid=)")
    run_shell_cmd(f"tsp -r {tsp_id}")
    job_dir = os.path.join(FILE_DIR, job_id)
    run_shell_cmd(f"rm -rf {job_dir}")


def status(params):
    """
    return contents of queue
    params is empty dict
    """
    jobs = Job.query.all()
    jobs = [job.job_id for job in jobs]
    # h-stats.sh queries trex for mining stats, so we only run it when trex is running
    if not jobs:
        khs_stats = run_shell_cmd("./h-stats.sh")
        if khs_stats:
            khs_stats = khs_stats.split()
        if len(khs_stats) == 2:
            global KHS
            global STATS
            KHS = float(khs_stats[0])
            STATS = json.loads(khs_stats[1])
    # TODO if running gpc, apply rentaflop multiplier to estimate additional crypto earnings
    
    return {"queue": jobs, "khs": KHS, "stats": STATS}


def _send_results(job_id):
    """
    send render results to servers, removing files and queue entry
    """
    job_dir = os.path.join(FILE_DIR, job_id)
    tgz_path = os.path.join(job_dir, "output.tar.gz")
    output = os.path.join(job_dir, "output")
    # zip and send output dir
    run_shell_cmd(f"tar -czf {tgz_path} {output}")
    sandbox_id = os.getenv("SANDBOX_ID")
    server_url = "https://portal.rentaflop.com/api/host/output"
    data = {"job_id": str(job_id), "sandbox_id": str(sandbox_id)}
    files = {'render_file': open(tgz_path, 'rb'), 'json': json.dumps(data)}
    requests.post(server_url, files=files)
    run_shell_cmd(f"rm -rf {job_dir}")
    _delete_job_with_id(job_id)


def handle_finished_jobs():
    """
    checks for any finished jobs and sends results back to servers
    cleans up and removes files afterwards
    starts crypto miner if all jobs are finished
    """
    # job ids in existence on the file system
    job_ids = os.listdir(FILE_DIR)
    for job_id in job_ids:
        # find finished jobs
        if os.path.exists(os.path.join(FILE_DIR, job_id, "finished.txt")):
            # send results, clean files, and remove job from queue
            _send_results(job_id)
            continue
        # set timeout on queued job and kill if exceeded time limit
        start_time = os.path.getmtime(os.path.join(FILE_DIR, job_id, "started.txt"))
        start_time = dt.datetime.fromtimestamp(start_time)
        current_time = dt.datetime.utcnow()
        timeout = dt.timedelta(hours=1)
        if timeout < (current_time-start_time):
            # remove job from queue
            deleted_tsp = _delete_job_with_id(job_id)
            if deleted_tsp is not None:
                pid = run_shell_cmd(f"tsp -p {deleted_tsp}")
                if pid is not None:
                    pid = pid.strip()
                    run_shell_cmd(f"kill -9 {pid}")
            # clean up files
            run_shell_cmd(f"rm -rf {job_dir}")

    # remove finished jobs from tsp queue
    run_shell_cmd("tsp -C", quiet=True)
    # not using ORM because this is run in separate thread where app/db are not defined
    conn = pymysql.connect(host='localhost', user='root', password = "sandbox", db='sandbox')
    cur = conn.cursor()
    n_jobs = cur.execute("SELECT * FROM job;")
    conn.close()
    if n_jobs == 0:
        # nothing left running in queue, so we mine crypto again
        start_mining()


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
                to_return = log_before_after(func, params)()
            except Exception as e:
                SANDBOX_LOGGER.exception(f"Caught exception: {e}")
        if to_return is not None:
            return to_return

        return jsonify("200")
    
    app.run(host='0.0.0.0', port=443, ssl_context='adhoc')


CMD_TO_FUNC = {
    "push": push_job,
    "pop": pop_job,
    "status": status,
}
FILE_DIR = "/root/jobs"
os.makedirs(FILE_DIR)
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

class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer)
    tsp_id = db.Column(db.Integer)
    job_dir = db.Column(db.String(64))

    def __repr__(self):
        return f"<Job {self.job_id} {self.tsp_id} {self.job_dir}>"

db.create_all(app=app)


def main():
    start_mining()
    # create a scheduler that periodically checks/handles finished jobs starts mining when there are no jobs in queue
    scheduler = APScheduler()
    scheduler.add_job(id='Handle Finished Jobs', func=handle_finished_jobs, trigger="interval", seconds=10)
    scheduler.start()
    q = multiprocessing.Queue()
    server = multiprocessing.Process(target=run_flask_server, args=(q,))
    server.start()


if __name__=="__main__":
    main()
