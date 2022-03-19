"""
runs listener within docker sandbox and queues compute jobs
mines crypto whenever queue is empty
"""
import subprocess
import multiprocessing
from flask import Flask, jsonify, request


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
    # TODO figure out args to run.py based on params
    tsp_id = run_shell_cmd(f"tsp python3 run.py").strip()
    # TODO track tsp_id so we can find it later


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
        func = CMD_TO_FUNC.get(cmd)
        func(params)

        return jsonify("200")
    
    app.run(host='0.0.0.0', port=443, ssl_context='adhoc')


CMD_TO_FUNC = {
    "push": push_job,
    "pop": pop_job,
}


def main():
    start_mining()
    app.secret_key = uuid.uuid4().hex
    q = multiprocessing.Queue()
    server = multiprocessing.Process(target=run_flask_server, args=(q,))
    server.start()


if __name__=="__main__":
    main()
