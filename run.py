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


def main():
    try:
        task_dir = sys.argv[1]
        start_frame = sys.argv[2]
        end_frame = sys.argv[3]
        uuid_str = sys.argv[4]
        blender_version = sys.argv[5]
        output_path = os.path.join(task_dir, "output/")
        os.mkdir(output_path)
        run_shell_cmd(f"touch {task_dir}/started.txt", quiet=True)
        check_blender(blender_version)
        run_shell_cmd(f"rm -r blender; mkdir blender && tar -xf blender-{blender_version}.tar.xz -C blender --strip-components 1", quiet=True)
        render_path = f"{task_dir}/render_file.blend"
        render_path2 = f"{task_dir}/render_file2.blend"
        de_script = f'''"import os; os.system('gpg --passphrase {uuid_str} --batch --no-tty -d {render_path} > {render_path2} && mv {render_path2} {render_path}')"'''
        # reformats videos to PNG
        fmt_script = f'''"import bpy; file_format = bpy.context.scene.render.image_settings.file_format; bpy.context.scene.render.image_settings.file_format = 'PNG' if file_format in ['FFMPEG', 'AVI_RAW', 'AVI_JPEG'] else file_format"'''
        rm_script = f'''"import os; os.remove('{render_path}')"'''
        render_config = subprocess.check_output(f"blender/blender --python-expr {de_script} -b {render_path} --python render_config.py", shell=True,
                                                encoding="utf8", stderr=subprocess.STDOUT)
        is_eevee = "Found render engine: BLENDER_EEVEE" in render_config

        # render results for specified frames to output path; disables scripting; if eevee is specified in blend file then it'll use eevee, even though cycles is specified here
        cmd = f"DISPLAY=:0.0 blender/blender -b {render_path} --python-expr {fmt_script} --python-expr {rm_script} -o {output_path} -s {start_frame} -e {end_frame} --disable-autoexec -a -- --cycles-device OPTIX"
        return_code = os.system(cmd)
        # successful render, so send result to servers
        if return_code == 0:
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
