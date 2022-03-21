"""
runs render job
usage:
    # job_dir is directory containing render file for job
    python3 run.py job_dir
"""
import sys
import os


def main():
    job_dir = sys.argv[1]
    output_path = os.path.join(job_dir, "output")
    os.mkdir(output_path)
    os.system(f"touch {job_dir}/started.txt")
    os.system(f"blender -b {job_dir}/render_file.blend -o {output_path} -a")
    # lets the sandbox queue know when the run is finished
    os.system(f"touch {job_dir}/finished.txt")

    
if __name__=="__main__":
    main()
