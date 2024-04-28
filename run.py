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
from utils import run_shell_cmd, calculate_frame_times
import glob
import traceback


def check_blender(target_version):
    """
    check for blender target_version installation and install if not found
    does nothing if target_version installed
    maintains a LRU cache of downloaded blender versions
    """
    file_path = f"blender-{target_version}.tar.xz"
    if os.path.exists(file_path):
        # update last modified time to now
        os.utime(file_path)
        
        return

    DAEMON_LOGGER.debug(f"Installing blender version {target_version}...")
    short_version = target_version.rpartition(".")[0]
    # go to https://download.blender.org/release/ to check blender version updates
    run_shell_cmd(f"wget https://download.blender.org/release/Blender{short_version}/blender-{target_version}-linux-x64.tar.xz -O blender.tar.xz && mv blender.tar.xz blender-{target_version}.tar.xz")

    # update cache, if necessary
    cache_size = 5
    list_of_files = glob.glob("blender-*.tar.xz")
    if len(list_of_files) <= cache_size:
        return

    # cache too large, need to remove LRU version
    file_modification_times = [os.path.getmtime(f) for f in list_of_files]
    least_to_most_recent = [f for _, f in sorted(zip(file_modification_times, list_of_files), key=lambda pair: pair[0])]
    lru_version = least_to_most_recent[0]
    run_shell_cmd(f"rm -rf {lru_version}")


def run_task(is_cpu=False, is_png=False):
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
    de_script = f""" "import os; os.system('''gpg --passphrase {uuid_str} --batch --no-tty -d '{render_path}' > '{render_path2}' ''')" """
    # reformats videos to PNG
    # fmt_script = f'''"import bpy; file_format = bpy.context.scene.render.image_settings.file_format; bpy.context.scene.render.image_settings.file_format = 'PNG' if file_format in ['FFMPEG', 'AVI_RAW', 'AVI_JPEG'] else file_format"'''
    rm_script = f'''"import os; os.remove('{render_path2}')"'''
    render_config = subprocess.check_output(f"{blender_path}/blender --python-expr {de_script} --disable-autoexec -noaudio -b '{render_path2}' --python render_config.py", shell=True,
                                            encoding="utf8", stderr=subprocess.STDOUT)
    is_eevee = "Found render engine: BLENDER_EEVEE" in render_config

    run_shell_cmd(f"touch {task_dir}/started_render.txt", quiet=True)
    sandbox_options = f"firejail --noprofile --net=none --caps.drop=all --private={task_dir} --blacklist=/"
    # render results for specified frames to output path; enables scripting; if eevee is specified in blend file then it'll use eevee, even though cycles is specified here
    cmd = f"DISPLAY=:0.0 {sandbox_options} {blender_path}/blender --enable-autoexec -noaudio -b '{render_path2}' --python-expr {rm_script} -o {output_path} -s {start_frame} -e {end_frame} -a --"
    # most of the time we run on GPU with OPTIX, but sometimes we run on cpu if not enough VRAM or other GPU issue
    if not is_cpu:
        cmd += " --cycles-device OPTIX"
    if is_png:
        cmd += " -F PNG"
    # send output to log file
    log_path = os.path.join(task_dir, "log.txt")
    try:
        with open(log_path, "w") as f:
            subprocess.run(cmd, shell=True, encoding="utf8", check=True, stderr=subprocess.STDOUT, stdout=f)

        # checking log tail because sometimes Blender throws and error and exits quietly without subprocess error
        log_tail = run_shell_cmd(f"tail {log_path}", very_quiet=True, format_output=False)
        if log_path and ("Error initializing video stream" in log_tail):
            raise subprocess.CalledProcessError(cmd=cmd, returncode=1, output=log_tail)
    except subprocess.CalledProcessError as e:
        log_tail = run_shell_cmd(f"tail {log_path}", very_quiet=True, format_output=False)
        # manually setting output to log file tail since everything is output to log file
        raise subprocess.CalledProcessError(cmd=e.cmd, returncode=e.returncode, output=log_tail)
    
    # successful render if no CalledProcessError, so send result to servers
    first_frame_time, subsequent_frames_avg = calculate_frame_times(task_dir, start_frame)
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
    try_with_png = False
    for i in range(2):
        try:
            run_task(is_cpu=try_with_cpu, is_png=try_with_png)
        except subprocess.CalledProcessError as e:
            DAEMON_LOGGER.error(f"Task execution command failed: {e}")
            DAEMON_LOGGER.error(f"Task execution command output: {e.output}")
            if e.output and ("Out of memory in CUDA queue enqueue" in e.output or "System is out of GPU memory" in e.output or \
                             "Invalid value in cuMemcpy2DUnaligned_v2" in e.output):
                try_with_cpu = True
                DAEMON_LOGGER.info("Ran out of VRAM so trying task again with CPU!")
            # NOTE: if error strings updated, see if they need to be updated in run_task too; sometimes blender exits quietly on error without subprocess error
            if e.output and ("Error initializing video stream" in e.output):
                try_with_png = True
                DAEMON_LOGGER.info("Issue with video format so trying task again with PNG!")
        except:
            error = traceback.format_exc()
            DAEMON_LOGGER.error(f"Exception during task execution: {error}")

        if not try_with_cpu and not try_with_png:
            break

    # lets the task queue know when the run is finished
    task_dir = sys.argv[1]
    run_shell_cmd(f"touch {task_dir}/finished.txt", quiet=True)


if __name__=="__main__":
    main()
