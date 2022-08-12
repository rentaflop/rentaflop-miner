"""
program for sending test queries to hosts
"""
import argparse
import json
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def parse_clargs():
    """
    parse and return command line args
    """
    parser = argparse.ArgumentParser(description='Test daemon queries')
    parser.add_argument('-c', '--cmd', help="command to send host")
    parser.add_argument('-p', '--params', help="params for command")
    parser.add_argument('-f', '--file', help="file for command")
    args = parser.parse_args()

    return args


def send_query(args):
    """
    construct and send query from args
    """
    url = "https://localhost:46443"
    with open("../rentaflop_config.json", "r") as f:
        config = json.load(f)
        rentaflop_id = config["rentaflop_id"]
    cmd = args.cmd
    params = args.params
    params = json.loads(params)
    render_file = args.file
    data = {"cmd": cmd, "params": params, "rentaflop_id": rentaflop_id}
    files = {"json": json.dumps(data)}
    if render_file:
        files['render_file'] = open(render_file, 'rb')
    
    response = requests.post(url, files=files, verify=False)
    print(response.text)


def main():
    args = parse_clargs()
    send_query(args)


if __name__=="__main__":
    main()
