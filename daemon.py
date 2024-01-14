"""
host daemon that communicates with rentaflop's servers for instructions
functions include, but are not limited to, software updates, system updates,
guest and crypto mining session initiation/termination, uninstallation
usage:
    python daemon.py
    # used to indicate rentaflop code update to version abc123 still in progress so we must call
    # update function again; not used during system updates or on second update
    python daemon.py update abc123
    # tell this process to do nothing but sleep for several seconds
    # used so hive won't try to start another process while this one is down for a software update
    python daemon.py sleep
"""


if __name__=="__main__":
    # we need this file as a wrapper to main.py because we need to be able to catch errors on new imports/syntax when new libraries are added
    # if there's an issue, install everything and try again
    # max sleep is 1 week
    # last-ditch effort to catch and fix code issues
    for _ in range(10080):
        try:
            import sys
            import importlib
            import main
            # ensure latest code changes are pulled each loop
            importlib.reload(main)
            from main import main
            main()

            # must exit here to prevent looping
            sys.exit(0)
        except (SyntaxError, ModuleNotFoundError, NameError, UnboundLocalError):
            import traceback
            import time
            import os
            import socket
            
            error = traceback.format_exc()
            print(f"Caught error in daemon: {error}")
            # for python import errors
            print("Running requirements.txt reinstallation...")            
            os.system("pip3 install -r requirements.txt")
            branch = "develop" if socket.gethostname() in ["rentaflop-one", "rentaflop_two", "rentaflop_three"] else "master"
            os.system(f"git checkout {branch}")
            os.system("git pull")
            time.sleep(180)
