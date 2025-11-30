import subprocess
import os
import sys
import signal
from flask_apscheduler import APScheduler
import datetime as dt
from config import DAEMON_LOGGER, IS_TEST_MODE
from utils import handle_pc_partial_frames, calculate_frame_times, get_last_frame_completed, create_frame_tarball, get_new_frames_for_upload, update_uploaded_frames_tracking, get_uploaded_frames
"""
defines db tables
"""
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
import boto3
import json


# Global variables to store subprocess and db context
RENDER_PROCESS = None
SIGNAL_DB = None
SIGNAL_APP = None
SIGNAL_TASK_ID = None

def signal_handler(signum, frame):
    """Handle SIGTERM and SIGCHLD signals"""
    global RENDER_PROCESS, SIGNAL_DB, SIGNAL_APP, SIGNAL_TASK_ID
    
    if signum == signal.SIGTERM:
        DAEMON_LOGGER.info("Received SIGTERM, checking task status before exit")
        # Check task status and mark as failed if not already in terminal state
        # This handles cases where spot instances are interrupted or container receives SIGTERM
        if SIGNAL_DB and SIGNAL_APP and SIGNAL_TASK_ID:
            try:
                with SIGNAL_APP.app_context():
                    class Task(SIGNAL_DB.Model):
                        __table__ = SIGNAL_DB.Model.metadata.tables["task"]
                    task = Task.query.filter_by(id=SIGNAL_TASK_ID).first()
                    if task and task.status not in ["stopped", "failed"]:
                        DAEMON_LOGGER.info(f"Marking task {SIGNAL_TASK_ID} as failed due to SIGTERM")
                        task.status = "failed"
                        task.stop_time = dt.datetime.utcnow()
                        SIGNAL_DB.session.commit()
                    elif task:
                        DAEMON_LOGGER.info(f"Task {SIGNAL_TASK_ID} already in terminal state: {task.status}")
            except Exception as e:
                DAEMON_LOGGER.error(f"Failed to update task status on SIGTERM: {e}")
        DAEMON_LOGGER.info("Exiting gracefully after SIGTERM")
        sys.exit(0)
    elif signum == signal.SIGCHLD:
        if RENDER_PROCESS is not None and RENDER_PROCESS.poll() is not None:
            exit_code = RENDER_PROCESS.returncode
            if exit_code == 0:
                DAEMON_LOGGER.info(f"run.py process completed successfully with exit code {exit_code}")
            else:
                DAEMON_LOGGER.error(f"run.py process terminated with exit code {exit_code}")
                # Update task status and stop time on failure signal capture
                if SIGNAL_DB and SIGNAL_APP and SIGNAL_TASK_ID:
                    try:
                        with SIGNAL_APP.app_context():
                            class Task(SIGNAL_DB.Model):
                                __table__ = SIGNAL_DB.Model.metadata.tables["task"]
                            task = Task.query.filter_by(id=SIGNAL_TASK_ID).first()
                            if task:
                                task.status = "failed"
                                task.stop_time = dt.datetime.utcnow()
                                SIGNAL_DB.session.commit()
                                DAEMON_LOGGER.info(f"Updated task {SIGNAL_TASK_ID} status to failed with stop time")
                    except Exception as e:
                        DAEMON_LOGGER.error(f"Failed to update task status on signal: {e}")
            DAEMON_LOGGER.info("Cloud native host exiting due to child process termination")
            sys.exit(exit_code)


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
        # NOTE: if timeout updated, make sure to also update in task_queue.py, retask_task lambda, and aws batch job def
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


def upload_frames_periodically(db, app, task_id):
    """
    periodic job that uploads any new frames as tarballs every 5 minutes
    only runs for cloud hosts, not miner hosts
    creates tarballs with naming convention: {job_id}/{task_id}_frames_{start}-{end}.tar.gz

    NOTE: Does not upload if frames include the final frame of the task - run.py will handle the final batch
    """
    try:
        with app.app_context():
            # NOTE: separate from miner host Task table; this one connects to backend db from cloud host
            class Task(db.Model):
                __table__ = db.Model.metadata.tables["task"]

            # get task object
            task = Task.query.filter_by(id=task_id).first()
            if not task:
                DAEMON_LOGGER.error(f"Task {task_id} not found in database")
                return

            job_id = task.job_id
            tasks_path = "tasks"
            task_dir = os.path.join(tasks_path, str(task.id))

            # Calculate the final frame number for this task
            final_frame = task.start_frame + task.n_frames - 1

            # get list of new frames that haven't been uploaded yet
            frame_files = get_new_frames_for_upload(task_dir, task.start_frame, None)

            if not frame_files:
                DAEMON_LOGGER.debug(f"No new frames to upload for task {task_id}")
                return

            # create a tarball of the frame files
            tarball_path, start_frame_num, end_frame_num = create_frame_tarball(
                task_dir, job_id, task_id, frame_files
            )

            if not tarball_path:
                DAEMON_LOGGER.error(f"Failed to create frame tarball for task {task_id}")
                return

            # Check if the final frame is included in this batch - if so, skip and let run.py handle it
            if end_frame_num >= final_frame:
                DAEMON_LOGGER.info(f"Frames {start_frame_num}-{end_frame_num} include final frame {final_frame}, skipping scheduler upload (run.py will handle)")
                return

            # upload to S3
            if not IS_TEST_MODE:
                try:
                    S3_CLIENT = boto3.client("s3", region_name="us-east-1")
                    s3_key = f"{job_id}/{os.path.basename(tarball_path)}"
                    DAEMON_LOGGER.info(f"Uploading frame tarball to S3: {s3_key}")
                    S3_CLIENT.upload_file(tarball_path, "rentaflop-render-output", s3_key)
                    DAEMON_LOGGER.info(f"Successfully uploaded frame tarball to S3: {s3_key}")

                    # update tracking to mark these frames as uploaded
                    update_uploaded_frames_tracking(task_dir, start_frame_num, end_frame_num)

                    # delete the tarball after successful upload
                    try:
                        os.remove(tarball_path)
                        DAEMON_LOGGER.debug(f"Deleted local tarball: {tarball_path}")
                    except Exception as e:
                        DAEMON_LOGGER.warning(f"Failed to delete local tarball {tarball_path}: {e}")

                except Exception as e:
                    DAEMON_LOGGER.error(f"Failed to upload frame tarball to S3: {e}")
            else:
                DAEMON_LOGGER.info(f"Test mode: would upload {tarball_path} to S3 at {job_id}/{os.path.basename(tarball_path)}")
                update_uploaded_frames_tracking(task_dir, start_frame_num, end_frame_num)

    except Exception as e:
        DAEMON_LOGGER.error(f"Error in upload_frames_periodically: {e}")


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
        n_frames_rendered = last_frame_completed - start_frame + 1 if last_frame_completed else None
        is_price_calculation = os.getenv("IS_PRICE_CALCULATION", "0") == "1"
        if is_price_calculation:
            n_frames_rendered = 1
        first_frame_time, subsequent_frames_avg = calculate_frame_times(task_dir, start_frame, n_frames_rendered=n_frames_rendered)
        if last_frame_completed:
            task.last_frame_completed = last_frame_completed
        if first_frame_time:
            task.first_frame_time = first_frame_time
        if subsequent_frames_avg:
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
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGCHLD, signal_handler)
    
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
    
    # Set global variables for signal handler
    SIGNAL_DB = db
    SIGNAL_APP = app
    SIGNAL_TASK_ID = task_id

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
        # Add periodic frame upload job that runs every 5 minutes
        first_upload_run_time = dt.datetime.now() + dt.timedelta(seconds=10)
        scheduler.add_job(id="FrameUpload", func=upload_frames_periodically, trigger="interval", seconds=300, max_instances=1, next_run_time=first_upload_run_time, kwargs={
            "db": db, "app": app, "task_id": task_id})
        scheduler.start()
        DAEMON_LOGGER.info("Scheduler started with Checkin (60s) and FrameUpload (300s) jobs, keeping process alive")
        
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
