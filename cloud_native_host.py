import subprocess
import os
import sys
import signal
from flask_apscheduler import APScheduler
import datetime as dt
from config import DAEMON_LOGGER, IS_TEST_MODE
from utils import handle_pc_partial_frames, calculate_frame_times, get_last_frame_completed
"""
defines db tables
"""
from flask import Flask
from flask_sqlalchemy import SQLAlchemy


# Global variable to store the subprocess
RENDER_PROCESS = None

def sigterm_handler(signum, frame):
    """Handle SIGTERM signal sent by run.py when task completes"""
    DAEMON_LOGGER.info("Received SIGTERM from run.py, exiting gracefully")
    sys.exit(0)


def _check_task_status(task, task_dir):
    """
    checks status of task running on this cloud host and timeout if applicable
    task times out after 24 hours or partial PC time limit
    returns True iff task reached terminal state (stopped or failed), False otherwise
    """
    global RENDER_PROCESS
    
    # Check if render process has completed; run.py handles db updates for exit code 0
    if RENDER_PROCESS is not None and RENDER_PROCESS.poll() is not None:
        exit_code = RENDER_PROCESS.returncode
        if exit_code == 0:
            DAEMON_LOGGER.info(f"run.py process completed successfully with exit code {exit_code}")
            return True
        else:
            DAEMON_LOGGER.error(f"run.py process terminated with exit code {exit_code}")
            task.stop_time = dt.datetime.utcnow()
            task.status = "failed"
            return True
    
    # check if task started
    if os.path.exists(os.path.join(task_dir, "started.txt")):
        # set timeout on queued task and kill if exceeded time limit
        start_time = os.path.getmtime(os.path.join(task_dir, "started.txt"))
        start_time = dt.datetime.fromtimestamp(start_time)
        # must use now instead of utcnow since getmtime is local timestamp on local filesystem timezone
        current_time = dt.datetime.now()
        # NOTE: if timeout updated, make sure to also update in task_queue.py and retask_task lambda
        timeout = dt.timedelta(hours=24)
        if timeout < (current_time-start_time):
            DAEMON_LOGGER.error(f"Task timed out! Exiting...")
            # update task status with failure
            task.stop_time = dt.datetime.utcnow()
            task.status = "failed"
            return True

        # check for PCs taking too long and stop after partial frame
        is_price_calculation = os.getenv("IS_PRICE_CALCULATION", "0") == "1"
        if is_price_calculation:
            # creates frame_seconds.txt and then task will finish on its own after pkill to blender
            handle_pc_partial_frames(task.id, task_dir)

            return False
        
        return False


def checkin(db, app, task_id):
    """
    periodic checkin to see where task progress is and update rentaflop db
    like a cron to do the following periodically: check task status, setting db attributes like in host.py and task.last_seen,
    creating frame_seconds.txt file for PC partial renders, and running pkill on PC partial renders after timeout
    """
    with app.app_context():
        # NOTE: separate from miner host Task table; this one connects to backend db from cloud host
        class Task(db.Model):
            __table__ = db.Model.metadata.tables["task"]

        # get task object
        task = Task.query.filter_by(id=task_id).first()
        task.last_seen = dt.datetime.utcnow()
        tasks_path = "tasks"
        task_dir = os.path.join(tasks_path, str(task.id))
        start_frame = task.start_frame
        last_frame_completed = get_last_frame_completed(task_dir, start_frame)
        first_frame_time, subsequent_frames_avg = calculate_frame_times(task_dir, start_frame)
        task.last_frame_completed = last_frame_completed
        task.first_frame_time = first_frame_time
        task.subsequent_frames_avg = subsequent_frames_avg
        is_finished = _check_task_status(task, task_dir)
        db.session.commit()
        
    if is_finished:
        DAEMON_LOGGER.info(f"Task {task_id} finished, cloud native host exiting")
        sys.exit(0)


def start_render_task():
    """
    run run.py as a background process
    """
    global RENDER_PROCESS
    
    if IS_TEST_MODE:
        # In test mode, run synchronously to get the result immediately
        DAEMON_LOGGER.info("Test mode: running run.py synchronously")
        result = subprocess.run(["python3", "run.py"], capture_output=True, text=True)
        if result.returncode != 0:
            DAEMON_LOGGER.error(f"run.py failed with exit code {result.returncode}")
            DAEMON_LOGGER.error(f"stdout: {result.stdout}")
            DAEMON_LOGGER.error(f"stderr: {result.stderr}")
    else:
        try:
            RENDER_PROCESS = subprocess.Popen(["python3", "run.py"])
            DAEMON_LOGGER.info(f"Started run.py with PID {RENDER_PROCESS.pid}")
        except Exception as e:
            DAEMON_LOGGER.error(f"Failed to start run.py: {e}")
            RENDER_PROCESS = None


if __name__ == "__main__":
    # Register signal handler for graceful shutdown when run.py completes
    signal.signal(signal.SIGTERM, sigterm_handler)
    
    database_url = os.getenv("database_url")
    task_id = os.getenv("task_id")
    # init flask sqlalchemy orm
    app = Flask(__name__)
    class Config(object):
        SQLALCHEMY_DATABASE_URI = database_url
        SQLALCHEMY_TRACK_MODIFICATIONS = False

    app.config.from_object(Config)
    with app.app_context():
        db = SQLAlchemy(app)
        db.metadata.reflect(bind=db.engine)

    start_render_task()
    
    if IS_TEST_MODE:
        # In test mode, exit after render task completes
        DAEMON_LOGGER.info("Test mode: exiting after render completion")
        sys.exit(0)
    else:
        first_run_time = dt.datetime.now() + dt.timedelta(seconds=5)
        scheduler = APScheduler()
        scheduler.add_job(id="Checkin", func=checkin, trigger="interval", seconds=60, max_instances=1, next_run_time=first_run_time, kwargs={
            "db": db, "app": app, "task_id": task_id})
        scheduler.start()
        DAEMON_LOGGER.info("Scheduler started, keeping process alive")
        
        # Keep the main thread alive so the scheduler can run
        # The checkin job will call sys.exit(0) when the task completes or times out
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            DAEMON_LOGGER.info("Received interrupt, shutting down scheduler")
            scheduler.shutdown()
            DAEMON_LOGGER.info("Cloud native host exiting due to keyboard interrupt")
            sys.exit(0)
