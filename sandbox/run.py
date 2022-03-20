"""
runs render job
usage:
    # file_path is blender file to render
    python3 run.py file_path
"""
import sys
import os


def main():
    file_path = sys.argv[1]
    dir_name = os.path.dirname(file_path)
    output_path = os.path.join(dir_name, "output")
    os.mkdir(output_path)
    os.system(f"blender -b {file_path} -o {output_path} -a")

    
if __name__=="__main__":
    main()
