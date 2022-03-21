"""
runs listener within docker sandbox and queues compute jobs
mines crypto whenever queue is empty
"""
import subprocess
import multiprocessing
from flask import Flask, jsonify, request
import os


app = Flask(__name__)


def run_shell_cmd(cmd):
    """
    run cmd and return output
    """
    output = ""
    try:
        output = subprocess.check_output(cmd, shell=True, encoding="utf8", stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        # ignore errors for now until we have a need for logging
        pass
    
    return output


def start_mining():
    """
    begin mining crypto, but only if not already mining
    """
    output = run_shell_cmd("pgrep nbminer")
    # if already running nbminer we do nothing, otherwise start miner
    if not output:
        run_shell_cmd("cd NBMiner_Linux && ./nbminer -c config.json &")


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
    with open(f"{job_dir}/render_file.blend", "w") as f:
        f.write(render_file)
    
    stop_mining()
    tsp_id = run_shell_cmd(f"tsp python3 run.py").strip()
    global QUEUE
    QUEUE.append({"job_dir": job_dir, "tsp_id": tsp_id})
    

def pop_job(params):
    """
    remove job from queue
    """
    # TODO figure out id based on params
    tsp_id = ""
    pid = run_shell_cmd(f"tsp -p {tsp_id}").strip()
    run_shell_cmd(f"kill -9 {pid}")
    # TODO stop tracking tsp_id


def run_flask_server(q):
    @app.route("/", methods=["POST"])
    def index():
        request_json = request.get_json()
        cmd = request_json.get("cmd")
        params = request_json.get("params")
        render_file = request.files.get("render_file", "")
        if render_file:
            params["render_file"] = render_file.read()
        
        func = CMD_TO_FUNC.get(cmd)
        func(params)

        return jsonify("200")
    
    app.run(host='0.0.0.0', port=443, ssl_context='adhoc')


CMD_TO_FUNC = {
    "push": push_job,
    "pop": pop_job,
}
QUEUE = []
FILE_DIR = "~/jobs"


def main():
    start_mining()
    app.secret_key = uuid.uuid4().hex
    q = multiprocessing.Queue()
    server = multiprocessing.Process(target=run_flask_server, args=(q,))
    server.start()


if __name__=="__main__":
    main()