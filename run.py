"""
runs render task
usage:
    # task_dir is directory containing render file for task
    python3 run.py task_dir start_frame end_frame
"""
import sys
import os
import requests
import json


def main():
    try:
        task_dir = sys.argv[1]
        start_frame = sys.argv[2]
        end_frame = sys.argv[3]
        output_path = os.path.join(task_dir, "output/")
        os.mkdir(output_path)
        os.system(f"touch {task_dir}/started.txt")
        # render results for specified frames to output path; disables scripting; if eevee is specified in blend file then it'll use eevee, even though cycles is specified here
        cmd = f"DISPLAY=:0.0 blender/blender -b {task_dir}/render_file.blend -o {output_path} -s {start_frame} -e {end_frame} --disable-autoexec -a -- --cycles-device OPTIX"
        return_code = os.system(cmd)
        # successful render, so send result to servers
        if return_code == 0:
            tgz_path = os.path.join(task_dir, "output.tar.gz")
            output = os.path.join(task_dir, "output")
            # zip and send output dir
            os.system(f"tar -czf {tgz_path} {output}")
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
            requests.post(server_url, json=data)
    except:
        pass
    finally:
        # lets the sandbox queue know when the run is finished
        os.system(f"touch {task_dir}/finished.txt")

    
if __name__=="__main__":
    main()
