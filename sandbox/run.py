"""
runs render task
usage:
    # task_dir is directory containing render file for task
    python3 run.py task_dir start_frame end_frame
"""
import sys
import os


def main():
    task_dir = sys.argv[1]
    start_frame = sys.argv[2]
    end_frame = sys.argv[3]
    output_path = os.path.join(task_dir, "output/")
    os.mkdir(output_path)
    os.system(f"touch {task_dir}/started.txt")
    return_code = os.system(f"blender/blender -b {task_dir}/render_file.blend -o {output_path} -s {start_frame} -e {end_frame} -a -- --cycles-device OPTIX")
    if return_code == 0:
        # lets the sandbox queue know when the run is finished
        os.system(f"touch {task_dir}/finished.txt")

    
if __name__=="__main__":
    main()
