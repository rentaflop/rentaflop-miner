"""
runs render task
usage:
    # task_dir is directory containing render file for task
    python3 run.py task_dir main_file_path start_frame end_frame uuid_str blender_version is_cpu cuda_visible_devices
    # running with IS_CLOUD_HOST=1, extracts params from env vars
    python3 run.py
"""
import sys
import os
import requests
import json
from config import DAEMON_LOGGER, IS_CLOUD_HOST, IS_TEST_MODE, FILENAME
import subprocess
from utils import run_shell_cmd, calculate_frame_times, post_to_rentaflop, get_rentaflop_id
import glob
import traceback
import boto3
import datetime as dt
"""
defines db tables
"""
from flask import Flask
from flask_sqlalchemy import SQLAlchemy


if IS_CLOUD_HOST:
    S3_CLIENT = boto3.client("s3", region_name="us-east-1")
    LAMBDA_CLIENT = boto3.client("lambda", region_name="us-east-1")


def check_blender(target_version):
    """
    check for blender target_version installation and install if not found
    does nothing if target_version installed
    maintains a LRU cache of downloaded blender versions
    """
    DAEMON_LOGGER.info(f"Checking Blender version {target_version}...")
    file_path = f"blender-{target_version}.tar.xz"
    if os.path.exists(file_path):
        DAEMON_LOGGER.info(f"Blender {target_version} already exists, updating access time")
        # update last modified time to now
        os.utime(file_path)
        
        return

    DAEMON_LOGGER.info(f"Blender {target_version} not found, downloading...")
    short_version = target_version.rpartition(".")[0]
    DAEMON_LOGGER.info(f"Downloading Blender {target_version} from blender.org...")
    # go to https://download.blender.org/release/ to check blender version updates
    run_shell_cmd(f"wget https://download.blender.org/release/Blender{short_version}/blender-{target_version}-linux-x64.tar.xz -O blender.tar.xz && mv blender.tar.xz blender-{target_version}.tar.xz")
    DAEMON_LOGGER.info(f"Blender {target_version} download completed")

    # update cache, if necessary
    cache_size = 5
    list_of_files = glob.glob("blender-*.tar.xz")
    DAEMON_LOGGER.info(f"Current cache has {len(list_of_files)} Blender versions, max is {cache_size}")
    if len(list_of_files) <= cache_size:
        return

    # cache too large, need to remove LRU version
    DAEMON_LOGGER.info("Cache is full, removing least recently used Blender version")
    file_modification_times = [os.path.getmtime(f) for f in list_of_files]
    least_to_most_recent = [f for _, f in sorted(zip(file_modification_times, list_of_files), key=lambda pair: pair[0])]
    lru_version = least_to_most_recent[0]
    DAEMON_LOGGER.info(f"Removing LRU Blender version: {lru_version}")
    run_shell_cmd(f"rm -rf {lru_version}")


def run_task(is_png=False, task_dir=None, db=None, app=None, task_id=None, start_frame=None, end_frame=None):
    """
    run rendering task
    """
    DAEMON_LOGGER.info(f"Starting run_task with task_id={task_id}, is_png={is_png}, start_frame={start_frame}, end_frame={end_frame}")
    
    if IS_CLOUD_HOST:
        blender_version = os.getenv("blender_version")
        is_cpu = True
        cuda_visible_devices = None
        DAEMON_LOGGER.info(f"Cloud host mode: blender_version={blender_version}, is_cpu={is_cpu}")
    else:
        task_dir = sys.argv[1]
        render_path = sys.argv[2]
        start_frame = int(sys.argv[3])
        end_frame = int(sys.argv[4])
        uuid_str = sys.argv[5]
        blender_version = sys.argv[6]
        is_cpu = sys.argv[7].lower() == "true"
        cuda_visible_devices = sys.argv[8]
        if cuda_visible_devices.lower() == "none":
            cuda_visible_devices = None
        DAEMON_LOGGER.info(f"Miner host mode: task_dir={task_dir}, blender_version={blender_version}, is_cpu={is_cpu}, cuda_visible_devices={cuda_visible_devices}")
    output_path = os.path.join(task_dir, "output/")
    blender_path = os.path.join(task_dir, "blender/")
    full_blender_path = "blender" if IS_TEST_MODE else os.path.join(blender_path, "blender")
    DAEMON_LOGGER.info(f"Setting up directories: output_path={output_path}, blender_path={blender_path}")
    os.makedirs(output_path, exist_ok=True)
    os.makedirs(blender_path, exist_ok=True)
    run_shell_cmd(f"touch {task_dir}/started.txt", quiet=True)
    DAEMON_LOGGER.info("Created task directories and marked as started")
    if IS_CLOUD_HOST:
        with app.app_context():
            # NOTE: separate from miner host Task table; this one connects to backend db from cloud host
            class Task(db.Model):
                __table__ = db.Model.metadata.tables["task"]
            
            task = Task.query.filter_by(id=task_id).first()
            task.status = "started"
            task.start_time = dt.datetime.utcnow()
            # must commit before any long-running commands are executed otherwise db connection will reset and we'll lose changes
            db.session.commit()
    else:
        check_blender(blender_version)
    
    if not IS_TEST_MODE:
        DAEMON_LOGGER.info(f"Extracting Blender {blender_version} to {blender_path}")
        run_shell_cmd(f"tar -xf blender-{blender_version}.tar.xz -C {blender_path} --strip-components 1", quiet=True)
        DAEMON_LOGGER.info("Blender extraction completed")
    if IS_CLOUD_HOST:
        input_path = os.path.join(task_dir, "input/")
        os.makedirs(input_path, exist_ok=True)
        extension = os.path.splitext(FILENAME)[1] if FILENAME else None
        is_zip = True if extension in [".zip"] else False
        saved_name = "render_file.zip" if is_zip else "render_file.blend"
        saved_path = os.path.join(input_path, saved_name)
        
        if IS_TEST_MODE:
            # In test mode, FILENAME is a local file path, use it directly
            DAEMON_LOGGER.info(f"Test mode: using local file {FILENAME} directly")
            saved_path = FILENAME
        else:
            DAEMON_LOGGER.info(f"Downloading render file {FILENAME} from S3 to {saved_path}")
            S3_CLIENT.download_file("rentaflop-render-uploads", FILENAME, saved_path)
            DAEMON_LOGGER.info("S3 download completed")
        render_path = saved_path
        if is_zip:
            DAEMON_LOGGER.info(f"Extracting zip file {saved_path} to {input_path}")
            subprocess.check_output(f"unzip {saved_path} -d {input_path}", shell=True, encoding="utf8", stderr=subprocess.STDOUT)
            # NOTE: duplicated in task_queue.py
            blend_files = glob.glob(os.path.join(input_path, '**', "*.blend*"), recursive=True)
            DAEMON_LOGGER.info(f"Found {len(blend_files)} blend files: {blend_files}")
            # prefer to use .blend instead of .blend1 if both found
            for blend_file in blend_files:
                render_path = blend_file
                if blend_file.endswith(".blend"):
                    DAEMON_LOGGER.info(f"Selected .blend file: {blend_file}")
                    break
            DAEMON_LOGGER.info(f"Final render path: {render_path}")

    # render_name, render_extension = os.path.splitext(render_path)
    # render_path2 = render_name + "2" + render_extension
    # de_script = f""" "import os; os.system('''gpg --passphrase {uuid_str} --batch --no-tty -d '{render_path}' > '{render_path2}' ''')" """
    # reformats videos to PNG
    # fmt_script = f'''"import bpy; file_format = bpy.context.scene.render.image_settings.file_format; bpy.context.scene.render.image_settings.file_format = 'PNG' if file_format in ['FFMPEG', 'AVI_RAW', 'AVI_JPEG'] else file_format"'''
    # rm_script = f'''"import os; os.remove('{render_path2}')"'''
    # NOTE: cannot pass additional args to blender after " -- " because the -- tells blender to ignore all subsequent args
    # render_config = subprocess.check_output(f"{blender_path}/blender --python-expr {de_script} --disable-autoexec -noaudio -b '{render_path2}' --python render_config.py -- {task_dir}", shell=True, encoding="utf8", stderr=subprocess.STDOUT)
    DAEMON_LOGGER.info(f"Analyzing render configuration for {render_path}")
    render_config = subprocess.check_output(f"{full_blender_path} --disable-autoexec -noaudio -b '{render_path}' --python render_config.py -- {task_dir}", shell=True, encoding="utf8", stderr=subprocess.STDOUT)
    eevee_name = "BLENDER_EEVEE"
    eevee_next_name = "BLENDER_EEVEE_NEXT"
    is_eevee = (f"Found render engine: {eevee_name}" in render_config) or (f"Found render engine: {eevee_next_name}" in render_config)
    engine_type = "Eevee" if is_eevee else "Cycles"
    DAEMON_LOGGER.info(f"Detected render engine: {engine_type}")

    run_shell_cmd(f"touch {task_dir}/started_render.txt", quiet=True)
    DAEMON_LOGGER.info(f"Starting render process for frames {start_frame}-{end_frame}")
    
    sandbox_options = f"firejail --noprofile --net=none --caps.drop=all --private={task_dir} --blacklist=/"
    # render results for specified frames to output path; enables scripting; if eevee is specified in blend file then it'll use eevee, even though cycles is specified here
    # cmd = f"DISPLAY=:0.0 {sandbox_options} {blender_path}/blender --enable-autoexec -noaudio -b '{render_path2}' --python-expr {rm_script} -o {output_path} -s {start_frame} -e {end_frame}{' -F PNG' if is_png else ''} -a --"
    cmd = f"DISPLAY=:0.0 {'' if IS_TEST_MODE else sandbox_options} {full_blender_path} --enable-autoexec -noaudio -b '{render_path}' -o {output_path} -s {start_frame} -e {end_frame}{' -F PNG' if is_png else ''} -a --"
    # most of the time we run on GPU with OPTIX, but sometimes we run on cpu if not enough VRAM or other GPU issue
    if not is_cpu:
        cmd += " --cycles-device OPTIX"
        DAEMON_LOGGER.info("Using GPU rendering with OPTIX")
    else:
        DAEMON_LOGGER.info("Using CPU rendering")
    if cuda_visible_devices:
        cmd = f"CUDA_VISIBLE_DEVICES={cuda_visible_devices} {cmd}"
        DAEMON_LOGGER.info(f"Using CUDA devices: {cuda_visible_devices}")
    
    DAEMON_LOGGER.info(f"Render command: {cmd}")
    # send output to log file
    log_path = os.path.join(task_dir, "log.txt")
    try:
        DAEMON_LOGGER.info("Starting Blender render subprocess...")
        with open(log_path, "w") as f:
            subprocess.run(cmd, shell=True, encoding="utf8", check=True, stderr=subprocess.STDOUT, stdout=f)
        DAEMON_LOGGER.info("Blender render subprocess completed successfully")

        # checking log tail because sometimes Blender throws an error and exits quietly without subprocess error
        log_tail = run_shell_cmd(f"tail {log_path}", very_quiet=True, format_output=False)
        if log_tail and ("Error initializing video stream" in log_tail or "Error: width not divisible by 2" in log_tail or \
                         "Error: height not divisible by 2" in log_tail):
            DAEMON_LOGGER.error(f"Found video format error in log: {log_tail}")
            raise subprocess.CalledProcessError(cmd=cmd, returncode=1, output=log_tail)
    except subprocess.CalledProcessError as e:
        # if process died with code 15, that means we preempted it with pkill for PC partial render timeout, so we do nothing
        if e.returncode != -15:
            DAEMON_LOGGER.error(f"Render process failed with return code {e.returncode}")
            log_tail = run_shell_cmd(f"tail {log_path}", very_quiet=True, format_output=False)
            # manually setting output to log file tail since everything is output to log file
            raise subprocess.CalledProcessError(cmd=e.cmd, returncode=e.returncode, output=log_tail)
        else:
            DAEMON_LOGGER.info("Render process was terminated (return code -15), likely due to timeout")

    # successful render, so send result (usually frames but sometimes partial PC time estimate) to servers
    output = os.path.join(task_dir, "output")
    has_finished_frames = False
    if os.listdir(output):
        has_finished_frames = True
        DAEMON_LOGGER.info(f"Render completed with finished frames in {output}")
    else:
        DAEMON_LOGGER.info("No finished frames found in output directory")
    
    # if this exists, then we only have partial PC frame output to report; ie there are no finished frames
    total_frame_seconds = None
    render_time_file_path = os.path.join(task_dir, "frame_seconds.txt")
    if os.path.exists(render_time_file_path):
        with open(render_time_file_path, "r") as f:
            total_frame_seconds = int(f.read())
        DAEMON_LOGGER.info(f"Found partial render time data: {total_frame_seconds} seconds")

    if has_finished_frames:
        DAEMON_LOGGER.info("Calculating frame rendering times...")
        first_frame_time, subsequent_frames_avg = calculate_frame_times(task_dir, start_frame, n_frames_rendered=(end_frame - start_frame + 1))
        DAEMON_LOGGER.info(f"Frame times - first: {first_frame_time}s, avg subsequent: {subsequent_frames_avg}s")
        
        tgz_path = os.path.join(task_dir, "output.tar.gz")
        old_dir = os.getcwd()
        os.chdir(task_dir)
        DAEMON_LOGGER.info("Creating output tarball...")
        # zip and check output dir
        run_shell_cmd(f"tar -czf output.tar.gz output", quiet=True)
        # check to ensure we're sending a correctly-zipped output to rentaflop servers
        incorrect_tar_output = run_shell_cmd("tar --compare --file=output.tar.gz", quiet=True)
        os.chdir(old_dir)
        if incorrect_tar_output:
            DAEMON_LOGGER.error(f"Output tarball validation failed: {incorrect_tar_output}")
            raise Exception("Output tarball doesn't match output frames!")
        DAEMON_LOGGER.info("Output tarball created and validated successfully")

    if IS_CLOUD_HOST:
        with app.app_context():
            task = Task.query.filter_by(id=task_id).first()
            if has_finished_frames:
                job_id = task.job_id
                if not IS_TEST_MODE:
                    DAEMON_LOGGER.info(f"Uploading output to S3: {job_id}/{task_id}.tar.gz")
                    # automatically retries 3 times with exponential backoff
                    S3_CLIENT.upload_file(tgz_path, "rentaflop-render-output", f"{job_id}/{task_id}.tar.gz")
                    DAEMON_LOGGER.info("S3 upload completed successfully")
                # set db task attributes following host_output.py
                task.status = "stopped"
                task.stop_time = dt.datetime.utcnow()
                task.first_frame_time = first_frame_time if has_finished_frames else 1.0
                task.subsequent_frames_avg = subsequent_frames_avg if has_finished_frames else 1.0

            if total_frame_seconds is not None:
                task.total_frame_seconds = total_frame_seconds

            db.session.commit()
        
        if not IS_TEST_MODE:
            # trigger job queue to check if this job finished
            payload = {"cmd": "check_finished", "params": {"task_id": task_id, "is_eevee": is_eevee}}
            LAMBDA_CLIENT.invoke(FunctionName="job-queue", InvocationType="Event", Payload=json.dumps(payload))
        # exits whole container task, not just subprocess
        DAEMON_LOGGER.info(f"Task {task_id} completed successfully, exiting container")
        os._exit(0)
    else:
        sandbox_id = os.getenv("SANDBOX_ID")
        server_url = "https://api.rentaflop.com/host/output"
        # first request to get upload location
        data = {"task_id": str(task_id), "sandbox_id": str(sandbox_id)}
        DAEMON_LOGGER.info(f"Requesting upload location for task {task_id}")
        response = requests.post(server_url, json=data)
        response_json = response.json()
        if has_finished_frames:
            storage_url, fields = response_json["url"], response_json["fields"]
            DAEMON_LOGGER.info(f"Got upload location, uploading to {storage_url}")
            # upload output to upload location
            # using curl instead of python requests because large files get overflowError: string longer than 2147483647 bytes
            fields_flags = " ".join([f"-F {k}={fields[k]}" for k in fields])
            # TODO check for errors like "could not resolve host" and retry a couple times
            run_shell_cmd(f"curl -X POST {fields_flags} -F file=@{tgz_path} {storage_url}", quiet=True)
            DAEMON_LOGGER.info("File upload to storage location completed")

        # confirm upload
        data["confirm"] = True
        if has_finished_frames:
            data["first_frame_time"] = first_frame_time
            data["subsequent_frames_avg"] = subsequent_frames_avg
        if total_frame_seconds is not None:
            data["total_frame_seconds"] = total_frame_seconds
        if is_eevee:
            data["is_eevee"] = True
        DAEMON_LOGGER.info(f"Confirming upload with data: {data}")
        requests.post(server_url, json=data)
        DAEMON_LOGGER.info("Upload confirmation sent successfully")


def get_scanned_settings(name, job_id, Settings):
    """
    check file upload scans in db for existence of completed scan for filename
    if job_id set, then we return settings this job is using otherwise return the original upload settings
    return scan json settings or None if scan does not exist (not finished or failed)
    NOTE: duplicated in backend utils.py
    """
    # NOTE: when changed, update config.py, blender_scanner.py, job_queue.py, and add latest version to scanner Dockerfile
    to_return = {"selected_version": "4.4.0", "frame_step": 1}
    settings = Settings.query.filter_by(job_id=job_id).first()
    # settings weren't parsed yet so we do nothing
    if not settings:
        return to_return
    
    settings = json.loads(settings.original_settings)
    # clean settings up for frontend
    to_return = {
        "name": name,
        "scene": settings.get("scene"),
        "version": settings.get("version", "0.0.0"),
        # NOTE: when changed, update config.py, blender_scanner.py, and add latest version to scanner Dockerfile
        "selected_version": settings.get("selected_version", "4.4.0"),
        "start_frame": settings.get("start_frame"),
        "end_frame": settings.get("end_frame"),
        "frame_step": settings.get("frame_step"),
        "n_frames": settings.get("n_frames"),
        "engine": settings.get("engine"),
        "output_file_format": settings.get("output_file_format"),
        "resolution_percentage": settings.get("resolution_percentage"),
        "resolution_x": settings.get("resolution_x"),
        "resolution_y": settings.get("resolution_y"),
        "cameras": settings.get("cameras") if "cameras" in settings else [],
        "selected_camera": settings.get("selected_camera"),
        "has_camera": settings.get("has_camera"),
        "pixel_samples": settings.get("pixel_samples"),
        "use_motion_blur": settings.get("use_motion_blur"),
        "use_compositing": settings.get("use_compositing"),
        "use_sequencer": settings.get("use_sequencer"),
        "use_stamp_note": settings.get("use_stamp_note"),
        "stamp_note": settings.get("stamp_note"),
        "use_noise_threshold": settings.get("use_noise_threshold"),
        "noise_threshold": settings.get("noise_threshold"),
        "simulations": settings.get("simulations"),
        "errors": [],
        "error_resolutions": [],
        "warnings": [],
        "warning_resolutions": []
    }
    # NOTE: partial duplicate in views.py, we have this one in case there was a default version (ie set-settings not used) different than the original upload
    # ensure eevee name matches blender version; UI always uses BLENDER_EEVEE but it needs to be BLENDER_EEVEE_NEXT if version 4.2
    maj_min_version = float(".".join(settings.get("selected_version", "0.0.0").split(".")[:-1]))
    if to_return["engine"] == "BLENDER_EEVEE" and maj_min_version >= 4.2:
        to_return["engine"] = "BLENDER_EEVEE_NEXT"

    # error checking
    # if settings already populated with errors, then these came from the scanner itself
    to_return["errors"] = settings.get("errors", [])
    to_return["error_resolutions"] = settings.get("error_resolutions", [])
    if not to_return["errors"] and not settings.get("has_camera"):
        to_return["errors"].append("No scene camera found!")
        to_return["error_resolutions"].append("Please add a camera and re-upload your project.")

    # warning checking
    missing_files = settings.get("missing_files")
    if missing_files:
        missing_file_strs = [f"Name: {os.path.basename(missing_file)}\nFile path: {missing_file}" for missing_file in missing_files]
        warning_str = "The following missing files were found:\n" + "\n".join(missing_file_strs)
        warning_resolution_str = "If these textures and assets are needed for your render, please make sure to pack them into your file or create a zip."
        to_return["warnings"].append(warning_str)
        to_return["warning_resolutions"].append(warning_resolution_str)

    missing_caches = settings.get("missing_caches")
    if missing_caches:
        missing_cache_strs = [f"Name: {os.path.basename(missing_cache)}\nFolder path: {missing_cache}" for missing_cache in missing_caches]
        warning_str = "The following missing simulation caches were found:\n" + "\n".join(missing_cache_strs)
        warning_resolution_str = "Please make sure to bake your simulations and zip your animation file along with its cache folders."
        to_return["warnings"].append(warning_str)
        to_return["warning_resolutions"].append(warning_resolution_str)

    return to_return


def main():
    DAEMON_LOGGER.info("Starting run.py to begin render")
    app, db, task_id, task_dir, start_frame, end_frame = [None]*6
    if IS_CLOUD_HOST:
        database_url = os.getenv("database_url")
        task_id = os.getenv("task_id")
        tasks_path = "tasks"
        os.makedirs(tasks_path, exist_ok=True)
        task_dir = os.path.join(tasks_path, str(task_id))
        os.makedirs(task_dir)
        # init flask sqlalchemy orm
        app = Flask(__name__)
        class Config(object):
            SQLALCHEMY_DATABASE_URI = database_url
            SQLALCHEMY_TRACK_MODIFICATIONS = False

        app.config.from_object(Config)
        with app.app_context():
            db = SQLAlchemy(app)
            db.metadata.reflect(bind=db.engine)
            # NOTE: separate from miner host Task table; this one connects to backend db from cloud host
            class Task(db.Model):
                __table__ = db.Model.metadata.tables["task"]
            class Settings(db.Model):
                __table__ = db.Model.metadata.tables["settings"]

            # get task and settings objects
            task = Task.query.filter_by(id=task_id).first()
            start_frame = task.start_frame
            end_frame = start_frame + task.n_frames - 1
            task.status = "queued"
            db.session.commit()
            name = "-".join(FILENAME.split("-")[1:])
            render_settings = get_scanned_settings(name, task.job_id, Settings)
            # save settings to task_dir/render_settings.json
            settings_path = os.path.join(task_dir, "render_settings.json")
            with open(settings_path, "w") as f:
                json.dump(render_settings, f)
    else:
        task_dir = sys.argv[1]
        task_id = os.path.basename(task_dir)
    
    try_with_png = False
    max_tries = 2
    for i in range(max_tries):
        try:
            run_task(is_png=try_with_png, task_dir=task_dir, db=db, app=app, task_id=task_id, start_frame=start_frame, end_frame=end_frame)
        except subprocess.CalledProcessError as e:
            DAEMON_LOGGER.error(f"Task execution command failed: Return code={e.returncode} {e}")
            DAEMON_LOGGER.error(f"Task execution command output: {e.output}")
            if e.output and ("Out of memory in CUDA queue enqueue" in e.output or "System is out of GPU memory" in e.output or \
                             "Invalid value in cuMemcpy2DUnaligned_v2" in e.output):
                DAEMON_LOGGER.info("Ran out of VRAM so we should try task again with CPU via retask directive if no other GPU hosts can handle render.")
            # NOTE: if error strings updated, see if they need to be updated in run_task too; sometimes blender exits quietly on error without subprocess error
            if e.output and ("Error initializing video stream" in e.output or "Error: width not divisible by 2" in e.output or \
                             "Error: height not divisible by 2" in e.output):
                try_with_png = True
                DAEMON_LOGGER.info("Issue with video format so trying task again with PNG!")

            # if loop isn't being run again, we send error message back to rentaflop
            if (not try_with_png) or i == (max_tries - 1):
                max_msg_len = 256
                # grab last max_msg_len characters from error message
                msg = ""
                if e.output:
                    msg = e.output
                    # remove unhelpful parts of error message
                    filter_msgs = ["Parent is shutting down, bye...", "shutting down the child process...", "The new log directory is"]
                    for filter_msg in filter_msgs:
                        msg = msg.split(filter_msg)[0]

                    msg = msg[-1 * max_msg_len:]

                if msg:
                    if not IS_CLOUD_HOST:
                        data = {"rentaflop_id": get_rentaflop_id(), "message": {"task_id": str(task_id), "type": "error", "message": msg}}
                        post_to_rentaflop(data, "daemon", quiet=False)

                if IS_CLOUD_HOST:
                    with app.app_context():
                        task = Task.query.filter_by(id=task_id).first()
                        # set task error message
                        task.error = msg
                        # task failed
                        task.status = "failed"
                        task.stop_time = dt.datetime.utcnow()
                        db.session.commit()
                    # exits whole container task, not just subprocess
                    DAEMON_LOGGER.error(f"Task {task_id} failed, exiting container with error")
                    os._exit(0)
        except:
            # catch all for logging misc errors that slipped through
            error = traceback.format_exc()
            DAEMON_LOGGER.error(f"Exception during task execution: {error}")

        if not try_with_png:
            break

    if not IS_CLOUD_HOST:
        # lets the task queue know when the run is finished
        run_shell_cmd(f"touch {task_dir}/finished.txt", quiet=True)


if __name__=="__main__":
    try:
        main()
    except Exception as e:
        error = traceback.format_exc()
        DAEMON_LOGGER.error(f"Fatal exception in run.py: {error}")
        
        # Try to mark task as failed if we're in cloud host mode
        if IS_CLOUD_HOST:
            try:
                database_url = os.getenv("database_url")
                task_id = os.getenv("task_id")
                if database_url and task_id:
                    app = Flask(__name__)
                    class Config(object):
                        SQLALCHEMY_DATABASE_URI = database_url
                        SQLALCHEMY_TRACK_MODIFICATIONS = False
                    app.config.from_object(Config)
                    with app.app_context():
                        db = SQLAlchemy(app)
                        db.metadata.reflect(bind=db.engine)
                        class Task(db.Model):
                            __table__ = db.Model.metadata.tables["task"]
                        task = Task.query.filter_by(id=task_id).first()
                        if task:
                            task.error = error[:256] if error else "Fatal error occurred"
                            task.status = "failed"
                            task.stop_time = dt.datetime.utcnow()
                            db.session.commit()
            except:
                DAEMON_LOGGER.error("Failed to update task status in database during fatal error handling")
    finally:
        if IS_CLOUD_HOST:
            # Always exit the container/process
            DAEMON_LOGGER.error("Fatal error in main, exiting container with code 1")
            os._exit(1)
