"""
host daemon that communicates with rentaflop's servers for instructions
functions include, but are not limited to, software updates, system updates,
guest and crypto mining session initiation/termination, uninstallation
"""


def mine(params):
    """
    handle commands related to mining, whether crypto mining or guest "mining"
    """
    pass


def update(params):
    """
    handle commands related to rentaflop software and system updates
    """
    pass


def uninstall(params):
    """
    uninstall rentaflop from this machine
    """
    pass


def main():
    cmd_to_func = {
        "mine": mine,
        "update": update,
        "uninstall": uninstall,
    }
    finished = False
    while not finished:
        # TODO either receive command or request it
        response = {"cmd": "mine", "params": {}}
        cmd = response["cmd"]
        params = response["params"]
        cmd_to_func[cmd](params)


if __name__=="__main__":
    main()
