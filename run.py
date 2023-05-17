"""
runs render task
usage:
    # task_dir is directory containing render file for task
    python3 run.py task_dir main_file_path start_frame end_frame uuid_str blender_version
"""
import sys
import os
import requests
import json
from config import DAEMON_LOGGER
import subprocess
from utils import run_shell_cmd
import datetime as dt
import glob
import traceback


def check_blender(target_version):
    """
    check for blender target_version installation and install if not found
    does nothing if target_version installed
    """
    if os.path.exists(f"blender-{target_version}.tar.xz"):
        return

    DAEMON_LOGGER.debug(f"Installing blender version {target_version}...")
    short_version = target_version.rpartition(".")[0]
    # go to https://download.blender.org/release/ to check blender version updates
    run_shell_cmd(f"wget https://download.blender.org/release/Blender{short_version}/blender-{target_version}-linux-x64.tar.xz -O blender.tar.xz && mv blender.tar.xz blender-{target_version}.tar.xz")


def calculate_frame_times(n_frames, task_dir):
    """
    calculate total time in minutes spent rendering frames, not including preprocessing
    requires render is completely finished and started_render.txt plus frames are still present
    return first_frame_time, subsequent_frames_avg
    """
    output_files = os.path.join(task_dir, "output/*")
    list_of_files = glob.glob(output_files)
    first_file = min(list_of_files, key=os.path.getmtime)
    last_file = max(list_of_files, key=os.path.getmtime)

    render_start_time = os.path.getmtime(os.path.join(task_dir, "started_render.txt"))
    render_start_time = dt.datetime.fromtimestamp(render_start_time)
    first_frame_finish = os.path.getmtime(first_file)
    first_frame_finish = dt.datetime.fromtimestamp(first_frame_finish)
    last_frame_finish = os.path.getmtime(last_file)
    last_frame_finish = dt.datetime.fromtimestamp(last_frame_finish)

    first_frame_duration = first_frame_finish-render_start_time
    first_frame_time = first_frame_duration.total_seconds()/60.0
    # handle 1-frame task edge case
    if n_frames == 1:
        subsequent_frames_avg = first_frame_time
    else:
        subsequent_frames_duration = last_frame_finish-first_frame_finish
        subsequent_frames_time = subsequent_frames_duration.total_seconds()/60.0
        subsequent_frames_avg = subsequent_frames_time / (n_frames - 1)

    return first_frame_time, subsequent_frames_avg


def run_task(is_cpu=False):
    """
    run rendering task
    """
    task_dir = sys.argv[1]
    render_path = sys.argv[2]
    start_frame = int(sys.argv[3])
    end_frame = int(sys.argv[4])
    uuid_str = sys.argv[5]
    blender_version = sys.argv[6]
    output_path = os.path.join(task_dir, "output/")
    blender_path = os.path.join(task_dir, "blender/")
    os.makedirs(output_path, exist_ok=True)
    os.makedirs(blender_path, exist_ok=True)
    run_shell_cmd(f"touch {task_dir}/started.txt", quiet=True)
    check_blender(blender_version)
    run_shell_cmd(f"tar -xf blender-{blender_version}.tar.xz -C {blender_path} --strip-components 1", quiet=True)
    render_name, render_extension = os.path.splitext(render_path)
    render_path2 = render_name + "2" + render_extension
    de_script = f'''"import os; os.system('gpg --passphrase {uuid_str} --batch --no-tty -d {render_path} > {render_path2}')"'''
    # reformats videos to PNG
    fmt_script = f'''"import bpy; file_format = bpy.context.scene.render.image_settings.file_format; bpy.context.scene.render.image_settings.file_format = 'PNG' if file_format in ['FFMPEG', 'AVI_RAW', 'AVI_JPEG'] else file_format"'''
    rm_script = f'''"import os; os.remove('{render_path2}')"'''
    render_config = subprocess.check_output(f"{blender_path}/blender --python-expr {de_script} --disable-autoexec -noaudio -b {render_path2} --python render_config.py", shell=True,
                                            encoding="utf8", stderr=subprocess.STDOUT)
    is_eevee = "Found render engine: BLENDER_EEVEE" in render_config

    run_shell_cmd(f"touch {task_dir}/started_render.txt", quiet=True)
    sandbox_options = f"firejail --noprofile --net=none --caps.drop=all --private={task_dir} --blacklist=/"
    # render results for specified frames to output path; enables scripting; if eevee is specified in blend file then it'll use eevee, even though cycles is specified here
    cmd = f"DISPLAY=:0.0 {sandbox_options} {blender_path}/blender --enable-autoexec -noaudio -b {render_path2} --python-expr {fmt_script} --python-expr {rm_script} -o {output_path} -s {start_frame} -e {end_frame} -a --"
    # most of the time we run on GPU with OPTIX, but sometimes we run on cpu if not enough VRAM or other GPU issue
    if not is_cpu:
        cmd += " --cycles-device OPTIX"
    cmd_output = subprocess.check_output(cmd, shell=True, encoding="utf8", stderr=subprocess.STDOUT)
    # successful render if no CalledProcessError, so send result to servers
    n_frames = end_frame - start_frame + 1
    first_frame_time, subsequent_frames_avg = calculate_frame_times(n_frames, task_dir)
    tgz_path = os.path.join(task_dir, "output.tar.gz")
    output = os.path.join(task_dir, "output")
    old_dir = os.getcwd()
    os.chdir(task_dir)
    # zip and send output dir
    run_shell_cmd(f"tar -czf output.tar.gz output", quiet=True)
    # check to ensure we're sending a correctly-zipped output to rentaflop servers
    incorrect_tar_output = run_shell_cmd("tar --compare --file=output.tar.gz", quiet=True)
    os.chdir(old_dir)
    if incorrect_tar_output:
        raise Exception("Output tarball doesn't match output frames!")

    sandbox_id = os.getenv("SANDBOX_ID")
    server_url = "https://api.rentaflop.com/host/output"
    task_id = os.path.basename(task_dir)
    # first request to get upload location
    data = {"task_id": str(task_id), "sandbox_id": str(sandbox_id)}
    response = requests.post(server_url, json=data)
    response_json = response.json()
    storage_url, fields = response_json["url"], response_json["fields"]
    # upload output to upload location
    # using curl instead of python requests because large files get overflowError: string longer than 2147483647 bytes
    fields_flags = " ".join([f"-F {k}={fields[k]}" for k in fields])
    run_shell_cmd(f"curl -X POST {fields_flags} -F file=@{tgz_path} {storage_url}", quiet=True)

    # confirm upload
    data["confirm"] = True
    data["first_frame_time"] = first_frame_time
    data["subsequent_frames_avg"] = subsequent_frames_avg
    if is_eevee:
        data["is_eevee"] = True
    requests.post(server_url, json=data)


def main():
    try_with_cpu = False
    for i in range(2):
        try:
            run_task(is_cpu=try_with_cpu)
        except subprocess.CalledProcessError as e:
            DAEMON_LOGGER.error(f"Task execution command failed: {e}")
            DAEMON_LOGGER.error(f"Task execution command output: {e.output}")
            if "Out of memory in CUDA queue enqueue" in e.output:
                try_with_cpu = True
                DAEMON_LOGGER.info("Ran out of VRAM so trying task again with CPU!")
        except:
            error = traceback.format_exc()
            DAEMON_LOGGER.error(f"Exception during task execution: {error}")

        if not try_with_cpu:
            break

    # lets the task queue know when the run is finished
    task_dir = sys.argv[1]
    run_shell_cmd(f"touch {task_dir}/finished.txt", quiet=True)


if __name__=="__main__":
    main()
