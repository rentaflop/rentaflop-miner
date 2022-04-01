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


def start_mining():
    """
    begin mining crypto, but only if not already mining
    """
    output = run_shell_cmd("pgrep nbminer")
    # if already running nbminer we do nothing, otherwise start miner
    if not output:
        # start with os.system since this needs to be run in background
        os.system("cd NBMiner_Linux && ./nbminer -c config.json --no-watchdog &")


def stop_mining():
    """
    stop crypto job
    """
    run_shell_cmd("pkill -f 'nbminer'")


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
    pid = run_shell_cmd(f"tsp -p {tsp_id}").strip()
    run_shell_cmd(f"kill -9 {pid}")
    job_dir = os.path.join(FILE_DIR, job_id)
    run_shell_cmd(f"rm -rf {job_dir}")


def status(params):
    """
    return contents of queue
    params is empty dict
    """
    jobs = Job.query.all()
    jobs = [job.job_id for job in jobs]
    
    return {"queue": jobs}


def _send_results(job_id):
    """
    send render results to servers, removing files and queue entry
    """
    job_dir = os.path.join(FILE_DIR, job_id)
    tgz_path = os.path.join(job_dir, "output.tar.gz")
    output = os.path.join(job_dir, "output")
    # zip and send output dir
    run_shell_cmd(f"tar -xzf {tgz_path} {output}")
    sandbox_id = os.getenv("SANDBOX_ID")
    server_url = "https://portal.rentaflop.com/api/host/output"
    data = {"job_id": str(job_id), "sandbox_id": str(sandbox_id)}
    files = {'render_file': open(tgz_path, 'rb'), 'json': json.dumps(data)}
    requests.post(server_url, files=files)
    run_shell_cmd(f"rm -rf {job_dir}")
    queued_job = _return_job_with_id(job_id)
    if queued_job:
        db.session.delete(queued_job)
        db.session.commit()


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
            queued_job = _return_job_with_id(job_id)
            if queued_job:
                tsp_id = queued_job.tsp_id
                # stop and remove relevant job from queue
                db.session.delete(queued_job)
                db.session.commit()
                pid = run_shell_cmd(f"tsp -p {tsp_id}").strip()
                run_shell_cmd(f"kill -9 {pid}")
            # clean up files
            run_shell_cmd(f"rm -rf {job_dir}")

    # remove finished jobs from tsp queue
    run_shell_cmd("tsp -C", quiet=True)
    # nothing left running in queue, so we mine crypto again
    jobs = Job.query.all()
    if not jobs:
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
        
        func = CMD_TO_FUNC.get(cmd)
        to_return = func(params)
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

class Config(object):
    SQLALCHEMY_DATABASE_URI = "mysql+pymysql://root:sandbox@localhost/sandbox"
    SECRET_KEY = uuid.uuid4().hex
    SQLALCHEMY_TRACK_MODIFICATIONS = False

os.system("/etc/init.d/mysql start")
app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)

class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer)
    tsp_id = db.Column(db.Integer)
    job_dir = db.Column(db.String)

    def __repr__(self):
        return f"<Job {self.job_id} {self.tsp_id} {self.job_dir}>"

db.create_all(app=app)


def main():
    start_mining()
    # create a scheduler that periodically checks/handles finished jobs starts mining when there are no jobs in queue
    scheduler = APScheduler()
    scheduler.add_job(id='Handle Finished Jobs', func=handle_finished_jobs, trigger="interval", seconds=15)
    scheduler.start()
    q = multiprocessing.Queue()
    server = multiprocessing.Process(target=run_flask_server, args=(q,))
    server.start()


if __name__=="__main__":
    main()
