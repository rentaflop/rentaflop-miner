"""
registers host with rentaflop
checks drive size, hardware virtualization, firewall support for UPnP, internet bandwidth
if no GPU instruct user to drive to micro center
usage:
    python host_registration.py wallet_address
"""
import sys
import os
import subprocess
import re


def register_host(wallet):
    """
    use wallet address to register host with rentaflop
    """
    # TODO get GPU, CPU, motherboard, hard drive, MAC address
    # post wallet address and above info to rentaflop server at https://portal.rentaflop.com/api/host/daemon
    # rentaflop to check if info already registered and return appropriate rentaflopID
    rentaflop_id = "TEST_RENTAFLOPID"
    # TODO also create rentaflop_api_key to act as password between rentaflop servers and hosts
    rentaflop_api_key = "TEST_RENTAFLOP_API_KEY"
    # TODO refactor to use daemon logger
    print(f"Connected to rentaflop, worker ID={rentaflop_id}")

    return rentaflop_id, rentaflop_api_key


def check_p2p():
    """
    ensure p2p connection requirements are met
    """
    # TODO refactor to use run_shell_cmd
    command_output = subprocess.check_output(["upnpc", "-s"]).decode("utf-8")
    if "No IGD UPnP Device found on the network" in command_output:
        print("Please ensure you have a router (or other device) configured to use UPnP on your network.")
        exit(1)

    print("Passed p2p connection check.")


def check_drive_size():
    """
    ensure storage requirements are met
    """
    min_drive_size = 25.0
    drive_info = subprocess.check_output(["fdisk", "-l"]).decode("utf-8") 
    # TODO run disk-expand utility from hive (or better, make it work on all linux distros)
    match = re.search("Disk /dev/sda: [+-]?([0-9]*[.])?[0-9]+", drive_info).group(0)
    drive_size = float(match.split()[-1])
    if drive_size < min_drive_size:
        print(f"Please ensure drive is at least {min_drive_size} GB.")
        exit(1)
        
    print("Passed drive size check.")

        
def check_bandwidth():
    """
    ensure download and upload speeds meet minimum requirements
    """
    min_download = 20
    min_upload = 1
    os.system("apt install python3-pip -y && pip3 install speedtest-cli")
    command_output = subprocess.check_output(["speedtest-cli", "--simple"]).decode("utf-8")
    _, download_line, upload_line = command_output.splitlines()
    download_match = re.search("Download: [+-]?([0-9]*[.])?[0-9]+", download_line).group(0)
    download = float(download_match.split()[-1])
    upload_match = re.search("Upload: [+-]?([0-9]*[.])?[0-9]+", upload_line).group(0)
    upload = float(upload_match.split()[-1])

    if download < min_download:
        print(f"Please ensure download speed is at least {min_download} mbps.")
        exit(1)
    if upload < min_upload:
        print(f"Please ensure upload speed is at least {min_upload} mbps.")
        exit(1)

    print("Passed bandwidth check.")
    

def _check_gpu_type():
    """
    ensure gpu resources are present
    return gpu found or None
    """
    # TODO remove 1080 if we're not going to make this a tier
    expected_gpus = ["GeForce GTX 1080", "Geforce GTX 1080 Ti", "GeForce RTX 2080 Ti", "GeForce RTX 3090"]
    command_output = subprocess.check_output(["nvidia-smi", "-L"]).decode("utf-8")
    # TODO check for specific, requested GPU rather than just any that meets our requirements
    for gpu in expected_gpus:
        if gpu in command_output:
            print(f"Found {gpu}.")

            return gpu

    return None


def _check_pcie(selected_gpu):
    """
    ensure gpu pcie resources pass minimum benchmark
    return total pcie bandwidth in GT/s
    """
    command_output = os.popen("lspci -vv | grep -P '[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]|LnkSta:'").read()
    command_output = command_output.splitlines()
    pci_info = None
    bandwidth = None
    for i, line in enumerate(command_output):
        if "VGA compatible controller" in line and selected_gpu in line and (i + 1) < len(command_output):
            pci_info = command_output[i + 1]
            break

    if pci_info:
        # get PCIe speed in GT/s (billion transactions per second) and number of lanes
        match = re.search("Speed [+-]?([0-9]*[.])?[0-9]+GT/s", pci_info).group(0)
        speed = float(match.split()[-1].replace("GT/s", ""))
        match = re.search("Width x[+-]?([0-9]*[.])?[0-9]+", pci_info).group(0)
        lanes = float(match.split()[-1].replace("x", ""))
        bandwidth = speed * lanes

    return bandwidth
    
    
def check_gpu_resources():
    """
    ensure gpu resources are present and minimum PCIe requirements are met
    benchmarking does not happen here, this is just a quick check for expected GPUs and PCIe
    """
    selected_gpu = _check_gpu_type()
    if not selected_gpu:
        print(f"Please ensure you have one of the following GPUs: {', '.join(expected_gpus)}.")
        exit(1)

    bandwidth = _check_pcie(selected_gpu)
    min_bandwidth = 8.0
    if not bandwidth or bandwidth < min_bandwidth:
        print(f"Please ensure GPU's total PCIe bandwidth is at least {min_bandwidth} GT/s.")
        exit(1)        
        
    print("Passed GPU resources check.")
    

def check_cpu_resources():
    """
    ensure cpu resources meet minimum requirements
    """
    min_cpus = 2.0
    command_output = subprocess.check_output(["nproc"]).decode("utf-8")
    cpus = float(command_output)
    if cpus < min_cpus:
        print(f"Please ensure your machine has at least {min_cpus} CPUs.")
        exit(1)

    print("Passed CPU resources check.")


def check_memory_resources():
    """
    ensure system has enough ram for rentaflop
    """
    min_ram = 8.0
    command_output = subprocess.check_output(["free", "--giga"]).decode("utf-8")
    command_output = command_output.splitlines()
    ram = float(command_output[1].split()[1])
    if ram < min_ram:
        print(f"Please ensure your machhine has at least {min_ram} GB of RAM.")
        exit(1)

    print("Passed memory resources check.")


def perform_host_requirement_checks():
    """
    performs all the necessary checks to ensure host can be added to rentaflop network
    """
    check_p2p()
    check_drive_size()
    check_bandwidth()
    check_gpu_resources()
    check_cpu_resources()
    check_memory_resources()
    
    
def main():
    wallet = sys.argv[1]
    register_host(wallet)
    perform_host_requirement_checks()
    print("Host registration successful!")


if __name__=="__main__":
    main()
