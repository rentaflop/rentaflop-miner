"""
tests for firejail sandbox
to test firejail configuration, run with:
firejail <my_params> python3 firejailtest.py
for instance:
firejail --private=. --net=none --caps.drop=all python3 firejailtest.py
you can also test the actual blender execution with:
DISPLAY=:0.0 firejail --noprofile --net=none --caps.drop=all --private=. --blacklist=/ blender/blender -b cityruntest.blend --python firejailtest.py -o output/ -s 1 -e 1 --disable-autoexec -a -- --cycles-device OPTIX
"""
import requests
import subprocess


# network access test
status_ok = False
try:
    response = requests.get("https://google.com")
    status_ok = response.ok
except:
    pass

if status_ok:
    print("Network still reachable, network test failed!")
else:
    print("Network test passed.")

# disk access test
secret_contents = ""
permitted_contents = ""
try:
    with open("../secret.txt", "r") as f:
        secret_contents = f.read()
except:
    pass
try:
    with open("permitted.txt", "r") as f:
        permitted_contents = f.read()
except:
    pass

if secret_contents:
    print("Secret file contents still readable, disk access test failed!")
if not permitted_contents:
    print("Permitted file contents not readable, disk access test failed!")
if not secret_contents and permitted_contents:
    print("Disk access test passed.")

# program access test
echo_output = ""
try:
    echo_output = subprocess.check_output("echo hello")
except:
    pass

if echo_output:
    print("Program calls still possible, program access test failed!")
else:
    print("Program access test passed.")
