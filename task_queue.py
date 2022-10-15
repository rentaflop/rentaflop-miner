"""
manages queue for compute tasks
"""
from config import DAEMON_LOGGER, app, db, Task
from utils import run_shell_cmd
import os
import datetime as dt
import requests
import tempfile
import uuid


def push_task(params):
    """
    add a task to the queue; could be benchmark or render
    """
    task_id = params.get("task_id")
    render_file = params.get("render_file")
    start_frame = params.get("start_frame")
    end_frame = params.get("end_frame")
    blender_version = params.get("blender_version")
    is_render = render_file is not None
    DAEMON_LOGGER.debug(f"Pushing task {task_id}...")
    # prevent duplicate tasks from being created in case of network delays or failures
    existing_task = Task.query.filter_by(task_id=task_id).first()
    if existing_task:
        DAEMON_LOGGER.info(f"Task {task_id} already in queue! Exiting...")
        return
    
    # create directory for task and write render file there
    task_dir = os.path.join(FILE_DIR, str(task_id))
    os.makedirs(task_dir)
    if is_render: 
        render_path = f"{task_dir}/render_file.blend"
        with open(render_path, "wb") as f:
            f.write(render_file)
            
        uuid_str = uuid.uuid4().hex
        os.system(f"gpg --passphrase {uuid_str} --batch --no-tty -c {render_path} && mv {render_path}.gpg {render_path}")
        task = Task(task_dir=task_dir, task_id=task_id, start_frame=start_frame, end_frame=end_frame, uuid_str=uuid_str, blender_version=blender_version)
    else:
        task = Task(task_dir=task_dir, task_id=task_id)
    
    db.session.add(task)
    db.session.commit()
    DAEMON_LOGGER.debug(f"Added task {task_id}")


def _delete_task_with_id(task_id):
    """
    delete task from db if it exists
    return task_dir if deleted, None otherwise
    """
    task = Task.query.filter_by(task_id=task_id).first()
    task_dir = None
    if task:
        task_dir = task.task_dir
        task = db.session.merge(task)
        db.session.delete(task)
        db.session.commit()

    return task_dir


def pop_task(params):
    """
    remove task from queue
    does nothing if already removed from queue
    """
    task_id = params["task_id"]
    DAEMON_LOGGER.debug(f"Popping task {task_id}...")
    task_dir = _delete_task_with_id(task_id)
    # kill task if running and clean up files
    run_shell_cmd(f'pkill -f "task_{task_id}"', very_quiet=True)
    run_shell_cmd('kill octane', very_quiet=True)
    run_shell_cmd(f"rm -rf {task_dir}", very_quiet=True)
    DAEMON_LOGGER.debug(f"Removed task {task_id}...")


def queue_status(params):
    """
    return contents of queue
    params is empty dict
    """
    tasks = Task.query.all()
    # must include benchmark so we can set status to gpc
    tasks = [task.task_id for task in tasks]
    # need this because connection pool not getting cleared for some reason
    db.close_all_sessions()
    
    return {"queue": tasks}


def _read_benchmark():
    """
    parse benchmark.txt file for benchmark info
    return obh value
    """
    benchmark = run_shell_cmd("awk '/Total score:/{getline; print}' octane/benchmark.txt", quiet=True, format_output=False).strip()

    return benchmark


def _handle_benchmark():
    """
    start benchmark task or check for benchmark output
    if output exists, send to rentaflop servers
    return True if benchmark task finished, False otherwise
    """
    # check if benchmark started and start if necessary
    if not os.path.exists("octane/started.txt"):
        run_shell_cmd("touch octane/started.txt", quiet=True)
        DAEMON_LOGGER.debug(f"Starting benchmark...")
        os.system("./octane/octane --benchmark -a octane/benchmark.txt --no-gui &")

        return False

    # check if benchmark still running
    if not os.path.exists("octane/benchmark.txt"):
        # set timeout on queued task and kill if exceeded time limit
        start_time = os.path.getmtime("octane/started.txt")
        start_time = dt.datetime.fromtimestamp(start_time)
        # must use now instead of utcnow since getmtime is local timestamp on local filesystem timezone
        current_time = dt.datetime.now()
        # timeout for benchmark is less than normal tasks
        timeout = dt.timedelta(minutes=20)
        if timeout < (current_time-start_time):
            # end benchmark task and let pop_task handle killing octane process
            DAEMON_LOGGER.info("Benchmark timed out! Exiting...")
            
            return True
        
        return False
    
    # benchmark job has finished running, so send output and exit container
    server_url = "https://api.rentaflop.com/host/output"
    benchmark = _read_benchmark()
    sandbox_id = os.getenv("SANDBOX_ID")
    data = {"benchmark": str(benchmark), "sandbox_id": str(sandbox_id)}
    DAEMON_LOGGER.debug(f"Sending benchmark score {benchmark} to servers")
    requests.post(server_url, json=data)
    DAEMON_LOGGER.debug("Finished benchmark")

    return True


def update_queue(params={}):
    """
    checks for any finished tasks and sends results back to servers
    cleans up and removes files afterwards
    starts the next task, if available
    """
    # get first queued task
    task = Task.query.first()
    if not task:
        return
    
    task_id = task.task_id
    # check if task finished
    if os.path.exists(os.path.join(task.task_dir, "finished.txt")):
        pop_task({"task_id": task_id})
        DAEMON_LOGGER.debug(f"Finished task {task_id}")
        
        # make another call to update_queue to start the next task immediately
        return update_queue()

    # check if task started
    if os.path.exists(os.path.join(task.task_dir, "started.txt")):
        # set timeout on queued task and kill if exceeded time limit
        start_time = os.path.getmtime(os.path.join(task.task_dir, "started.txt"))
        start_time = dt.datetime.fromtimestamp(start_time)
        # must use now instead of utcnow since getmtime is local timestamp on local filesystem timezone
        current_time = dt.datetime.now()
        # if timeout updated, make sure to also update in retask_task lambda
        timeout = dt.timedelta(hours=2)
        if timeout < (current_time-start_time):
            DAEMON_LOGGER.info(f"Task timed out! Exiting...")
            pop_task({"task_id": task_id})
            
            return update_queue()

        return

    # task_id will be -1 iff benchmark task
    if task_id == -1:
        is_finished = _handle_benchmark()
        if is_finished:
            pop_task({"task_id": task_id})
            # delete these after removing from db so we don't start it again
            run_shell_cmd('rm octane/started.txt', quiet=True)
            run_shell_cmd('rm octane/benchmark.txt', quiet=True)
            
            return update_queue()

        return
    
    # start task in bg
    DAEMON_LOGGER.debug(f"Starting task {task_id}...")
    os.system(f"python3 run.py {task.task_dir} {task.start_frame} {task.end_frame} {task.uuid_str} {task.blender_version} &")


# create tmp dir that's cleaned up when TEMP_DIR is destroyed
TEMP_DIR = tempfile.TemporaryDirectory()
FILE_DIR = TEMP_DIR.name
