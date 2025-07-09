"""
manages queue for compute tasks
"""
from config import DAEMON_LOGGER, app, db, Task
from utils import run_shell_cmd, calculate_frame_times, get_last_frame_completed
import os
import datetime as dt
import requests
import tempfile
import uuid
import pymysql
import json
import glob


def push_task(params):
    """
    add a task to the queue; could be benchmark or render
    """
    task_id = params.get("task_id")
    is_render = params.get("is_render")
    # user-edited settings overrides for start and end frame are provided here by backend; render_settings values are not necessarily correct for this task
    start_frame = params.get("start_frame")
    end_frame = params.get("end_frame")
    blender_version = params.get("blender_version")
    is_cpu = params.get("is_cpu")
    is_cpu = 1 if is_cpu else 0
    cuda_visible_devices = params.get("cuda_visible_devices")
    cuda_visible_devices = "NULL" if not cuda_visible_devices else f'"{cuda_visible_devices}"'
    render_settings = params.get("render_settings", {})
    file_cached_dir = params.get("file_cached_dir")
    is_price_calculation = params.get("is_price_calculation")
    is_price_calculation = 1 if is_price_calculation else 0
    DAEMON_LOGGER.debug(f"Pushing task {task_id}...")
    # prevent duplicate tasks from being created in case of network delays or failures
    with app.app_context():
        existing_task = Task.query.filter_by(task_id=task_id).first()
    if existing_task:
        DAEMON_LOGGER.info(f"Task {task_id} already in queue! Exiting...")
        return
    
    # create directory for task and write files there
    task_dir = os.path.join(FILE_DIR, str(task_id))
    os.makedirs(task_dir)
    # create task straight away to add it to queue so we don't restart crypto miner if we have to take a few minutes to process a large render file
    with app.app_context():
        task = Task(task_dir=task_dir, task_id=task_id, is_price_calculation=is_price_calculation)
        db.session.add(task)
        db.session.commit()
    if is_render:
        with open(f"{task_dir}/render_settings.json", "w") as f:
            json.dump(render_settings, f)

        # NOTE: duplicated in run.py
        blend_files = glob.glob(os.path.join(file_cached_dir, '**', "*.blend*"), recursive=True)
        # TODO only using one blend found but it may be a good idea to detect all blends at some point
        render_path = ""
        # prefer to use .blend instead of .blend1 if both found
        for blend_file in blend_files:
            render_path = blend_file
            if blend_file.endswith(".blend"):
                break
            
        uuid_str = uuid.uuid4().hex
        # TODO figure out encryption through use of cache
        # os.system(f"gpg --passphrase {uuid_str} --batch --no-tty -c '{render_path}' && mv '{render_path}.gpg' '{render_path}'")
        task_id = int(task_id)
        sql1 = f'UPDATE task SET main_file_path="{render_path}" WHERE task_id={task_id}'
        sql2 = f'UPDATE task SET start_frame={start_frame} WHERE task_id={task_id}'
        sql3 = f'UPDATE task SET end_frame={end_frame} WHERE task_id={task_id}'
        sql4 = f'UPDATE task SET uuid_str="{uuid_str}" WHERE task_id={task_id}'
        sql5 = f'UPDATE task SET blender_version="{blender_version}" WHERE task_id={task_id}'
        sql6 = f'UPDATE task SET is_cpu={is_cpu} WHERE task_id={task_id}'
        sql7 = f'UPDATE task SET cuda_visible_devices={cuda_visible_devices} WHERE task_id={task_id}'
        # updating task with pymysql instead of flask sqlalchemy because the initial commit in this function is causing the connection to sometimes close,
        # and trying to use it again here fails intermittently
        connection = pymysql.connect(host="localhost", user="root", password="daemon", database="daemon")
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(sql1)
                cursor.execute(sql2)
                cursor.execute(sql3)
                cursor.execute(sql4)
                cursor.execute(sql5)
                cursor.execute(sql6)
                cursor.execute(sql7)
            
            connection.commit()
    
    DAEMON_LOGGER.debug(f"Added task {task_id}")


def _delete_task_with_id(task_id):
    """
    delete task from db if it exists
    return task_dir if deleted, None otherwise
    """
    with app.app_context():
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
    with app.app_context():
        task_running = Task.query.first()
    is_currently_running = True if task_running and task_running.id == task_id else False
    task_dir = _delete_task_with_id(task_id)
    # kill task if running and clean up files
    if is_currently_running:
        run_shell_cmd(f'pkill -f "task_{task_id}"', very_quiet=True)
        run_shell_cmd('pkill -f octane', very_quiet=True)
        run_shell_cmd('pkill -f blender', very_quiet=True)
    run_shell_cmd(f"rm -rf {task_dir}", very_quiet=True)
    DAEMON_LOGGER.debug(f"Removed task {task_id}...")


def queue_status(params):
    """
    return contents of queue
    params is empty dict
    """
    with app.app_context():
        tasks = Task.query.all()
    # must include benchmark so we can set status to gpc
    task_ids = [task.task_id for task in tasks]
    last_frame_completed, first_frame_time, subsequent_frames_avg = [None] * 3
    try:
        if tasks:
            last_frame_completed = get_last_frame_completed(tasks[0].task_dir, tasks[0].start_frame)
            first_frame_time, subsequent_frames_avg = calculate_frame_times(tasks[0].task_dir, tasks[0].start_frame)
    except Exception as e:
        DAEMON_LOGGER.exception(f"Caught exception in queue status: {e}")

    # need this because connection pool not getting cleared for some reason
    with app.app_context():
        db.close_all_sessions()
    
    return {"queue": task_ids, "last_frame_completed": last_frame_completed, "first_frame_time": first_frame_time, \
            "subsequent_frames_avg": subsequent_frames_avg}


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


def _compute_seconds(time_str):
    """
    turns 00:35.12 like strings into total number of seconds, rounded
    """
    parts = time_str.split(':')
    
    # MM:SS.xx
    if len(parts) == 2:
        minutes, seconds = parts
        total_seconds = int(minutes) * 60 + float(seconds)
    # HH:MM:SS.xx
    else:
        hours, minutes, seconds = parts
        total_seconds = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    return round(total_seconds)


def _get_samples_render_metadata(samples_metadata):
    """
    return n_samples_rendered, seconds_rendering, seconds_remaining
    samples_metadata looks like:
    Fra:8 Mem:256.60M (Peak 256.60M) | Time:00:02.51 | Mem:62.11M, Peak:62.11M | Scene, ViewLayer | Sample 0/500000
    Fra:0 Mem:184.79M (Peak 184.79M) | Time:00:35.12 | Remaining:04:12.77 | Mem:550.87M, Peak:550.87M | Scene, ViewLayer | Sample 496/4096
    """
    n_samples_rendered = int(samples_metadata.split("Sample ")[1].split("/")[0])
    seconds_rendering = _compute_seconds(samples_metadata.split("Time:")[1].split(" |")[0])
    seconds_remaining = None
    if "Remaining:" in samples_metadata:
        seconds_remaining = _compute_seconds(samples_metadata.split("Remaining:")[1].split(" |")[0])
    
    return n_samples_rendered, seconds_rendering, seconds_remaining


def _handle_pc_partial_frames(task):
    """
    check if task has been rendering samples (ie not since render command started but since samples started being calculated) for longer than 5 minute timeout
    if so, terminate the rendering process so we can report extrapolated frame render time
    requires that task is a price calculation
    returns True if we stopped the rendering, False otherwise
    """
    # read log file and parse out metadata for samples and time rendered
    log_path = os.path.join(task.task_dir, "log.txt")
    # output is first and last render samples lines
    first_last_samples_metadata = run_shell_cmd(f"grep -E 'Sample [0-9]+/[0-9]+$' {log_path} | sed -e 1b -e '$!d'", quiet=True, format_output=False)
    if not first_last_samples_metadata or len(first_last_samples_metadata.splitlines()) != 2:
        return False

    first_samples_metadata, last_samples_metadata = first_last_samples_metadata.splitlines()
    _, samples_calc_start_seconds, _ = _get_samples_render_metadata(first_samples_metadata)
    n_samples_rendered, samples_calc_current_seconds, frame_seconds_remaining = _get_samples_render_metadata(last_samples_metadata)
    seconds_spent_rendering_samples = samples_calc_current_seconds - samples_calc_start_seconds

    # check for 5 minutes of rendering samples (not including Blender loading file, preprocessing, etc); don't stop render if only a minute left
    pc_samples_calc_timeout = 300
    if seconds_spent_rendering_samples > pc_samples_calc_timeout and frame_seconds_remaining > 60:
        # echo to a file the total seconds it would've taken to finish this frame so we can send back to servers
        start_time = os.path.getmtime(os.path.join(task.task_dir, "started.txt"))
        start_time = dt.datetime.fromtimestamp(start_time)
        # must use now instead of utcnow since getmtime is local timestamp on local filesystem timezone
        current_time = dt.datetime.now()
        total_frame_seconds = round((current_time - start_time).total_seconds() + frame_seconds_remaining)
        run_shell_cmd(f"echo {total_frame_seconds} > {task.task_dir}/frame_seconds.txt", quiet=True)
        DAEMON_LOGGER.info(f"Price calculation timeout exceeded, finishing task {task.task_id}...")
        # stops the blender process to trigger task output and cleanup
        run_shell_cmd("pkill -f blender")

        return True

    return False


def update_queue(params={}):
    """
    checks for any finished tasks and sends results back to servers
    cleans up and removes files afterwards
    starts the next task, if available
    """
    # get first queued task
    with app.app_context():
        task = Task.query.first()
    if not task:
        return
    
    task_id = task.task_id
    # check if task finished
    if os.path.exists(os.path.join(task.task_dir, "finished.txt")):
        # TODO check for existence of frame_seconds.txt and handle partial PC output
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
        # NOTE: if timeout updated, make sure to also update in retask_task lambda
        timeout = dt.timedelta(hours=24)
        if timeout < (current_time-start_time):
            DAEMON_LOGGER.info(f"Task timed out! Exiting...")
            pop_task({"task_id": task_id})
            
            return update_queue()

        # check for PCs taking too long and stop after partial frame
        if task.is_price_calculation:
            _handle_pc_partial_frames(task)
        
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

    # task exists in db, but now we check to see if fields are set and it's ready to be started
    if task.uuid_str:
        # start task in bg
        DAEMON_LOGGER.debug(f"Starting task {task_id}...")
        cmd = f"python3 run.py {task.task_dir} '{task.main_file_path}' {task.start_frame} {task.end_frame} {task.uuid_str} {task.blender_version}"
        # task directives
        cmd += f" {task.is_cpu} {task.cuda_visible_devices}"
        # run in background
        cmd += " &"
        os.system(cmd)


# create tmp dir that's cleaned up when TEMP_DIR is destroyed
TEMP_DIR = tempfile.TemporaryDirectory()
FILE_DIR = TEMP_DIR.name
