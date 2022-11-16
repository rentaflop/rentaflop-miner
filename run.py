"""
runs render task
usage:
    # task_dir is directory containing render file for task
    python3 run.py task_dir start_frame end_frame uuid_str blender_version
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
    subsequent_frames_duration = last_frame_finish-first_frame_finish
    subsequent_frames_time = subsequent_frames_duration.total_seconds()/60.0
    subsequent_frames_avg = subsequent_frames_time / (n_frames - 1)

    return first_frame_time, subsequent_frames_avg


def main():
    try:
        task_dir = sys.argv[1]
        start_frame = int(sys.argv[2])
        end_frame = int(sys.argv[3])
        uuid_str = sys.argv[4]
        blender_version = sys.argv[5]
        output_path = os.path.join(task_dir, "output/")
        blender_path = os.path.join(task_dir, "blender/")
        os.mkdir(output_path)
        os.mkdir(blender_path)
        run_shell_cmd(f"touch {task_dir}/started.txt", quiet=True)
        check_blender(blender_version)
        run_shell_cmd(f"tar -xf blender-{blender_version}.tar.xz -C {blender_path} --strip-components 1", quiet=True)
        render_path = f"{task_dir}/render_file.blend"
        render_path2 = f"{task_dir}/render_file2.blend"
        de_script = f'''"import os; os.system('gpg --passphrase {uuid_str} --batch --no-tty -d {render_path} > {render_path2} && mv {render_path2} {render_path}')"'''
        # reformats videos to PNG
        fmt_script = f'''"import bpy; file_format = bpy.context.scene.render.image_settings.file_format; bpy.context.scene.render.image_settings.file_format = 'PNG' if file_format in ['FFMPEG', 'AVI_RAW', 'AVI_JPEG'] else file_format"'''
        rm_script = f'''"import os; os.remove('{render_path}')"'''
        render_config = subprocess.check_output(f"{blender_path}/blender --python-expr {de_script} --disable-autoexec -b {render_path} --python render_config.py", shell=True,
                                                encoding="utf8", stderr=subprocess.STDOUT)
        is_eevee = "Found render engine: BLENDER_EEVEE" in render_config

        run_shell_cmd(f"touch {task_dir}/started_render.txt", quiet=True)
        sandbox_options = f"firejail --noprofile --net=none --caps.drop=all --private={task_dir} --blacklist=/"
        # render results for specified frames to output path; enables scripting; if eevee is specified in blend file then it'll use eevee, even though cycles is specified here
        cmd = f"DISPLAY=:0.0 {sandbox_options} {blender_path}/blender --enable-autoexec -b {render_path} --python-expr {fmt_script} --python-expr {rm_script} -o {output_path} -s {start_frame} -e {end_frame} -a -- --cycles-device OPTIX"
        return_code = os.system(cmd)
        # successful render, so send result to servers
        if return_code == 0:
            n_frames = end_frame - start_frame + 1
            first_frame_time, subsequent_frames_avg = calculate_frame_times(n_frames, task_dir)
            tgz_path = os.path.join(task_dir, "output.tar.gz")
            output = os.path.join(task_dir, "output")
            # zip and send output dir
            run_shell_cmd(f"tar -czf {tgz_path} {output}", quiet=True)
            sandbox_id = os.getenv("SANDBOX_ID")
            server_url = "https://api.rentaflop.com/host/output"
            task_id = os.path.basename(task_dir)
            # first request to get upload location
            data = {"task_id": str(task_id), "sandbox_id": str(sandbox_id)}
            response = requests.post(server_url, json=data)
            response_json = response.json()
            storage_url, fields = response_json["url"], response_json["fields"]
            # upload output to upload location
            with open(tgz_path, 'rb') as f:
                files = {'file': f}
                storage_response = requests.post(storage_url, data=fields, files=files)

            # confirm upload
            data["confirm"] = True
            data["first_frame_time"] = first_frame_time
            data["subsequent_frames_avg"] = subsequent_frames_avg
            if is_eevee:
                data["is_eevee"] = True
            requests.post(server_url, json=data)
        else:
            DAEMON_LOGGER.error(f"Task execution command failed with code {return_code}!")
    except:
        error = traceback.format_exc()
        DAEMON_LOGGER.error(f"Exception during task execution: {error}")
    finally:
        # lets the task queue know when the run is finished
        run_shell_cmd(f"touch {task_dir}/finished.txt", quiet=True)

    
if __name__=="__main__":
    main()
