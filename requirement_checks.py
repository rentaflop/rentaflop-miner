"""
performs minimum system requirement checks to host on rentaflop
checks drive size, hardware virtualization, firewall support for UPnP, internet bandwidth, CPU, etc.
if no GPU, instruct user to drive to micro center
"""
import sys
import os
import re
from config import DAEMON_LOGGER
from utils import run_shell_cmd


daemon_log_func = {"DEBUG": DAEMON_LOGGER.debug, "INFO": DAEMON_LOGGER.info, "WARNING": DAEMON_LOGGER.warning,
                   "ERROR": DAEMON_LOGGER.error, "CRITICAL": DAEMON_LOGGER.critical}


def _log_and_print(include_stdout, level, msg):
    """
    logs msg to daemon logger at level; optionally also print to stdout
    """
    if level not in daemon_log_func:
        level = "INFO"

    daemon_log_func[level](msg)
    if include_stdout:
        print(msg)

    
def check_p2p(include_stdout=False):
    """
    ensure p2p connection requirements are met
    """
    # TODO refactor to use run_shell_cmd
    command_output = subprocess.check_output(["upnpc", "-s"]).decode("utf-8")
    if "No IGD UPnP Device found on the network" in command_output:
        _log_and_print(include_stdout, "INFO", "Please ensure you have a router (or other device) configured to use UPnP on your network.")

    _log_and_print(include_stdout, "DEBUG", "Passed p2p connection check.")


def check_drive_size(include_stdout=False):
    """
    ensure storage requirements are met
    """
    min_drive_size = 25.0
    drive_info = subprocess.check_output(["fdisk", "-l"]).decode("utf-8") 
    # TODO run disk-expand utility from hive (or better, make it work on all linux distros)
    match = re.search("Disk /dev/sda: [+-]?([0-9]*[.])?[0-9]+", drive_info).group(0)
    drive_size = float(match.split()[-1])
    if drive_size < min_drive_size:
        _log_and_print(include_stdout, "INFO", f"Please ensure drive is at least {min_drive_size} GB.")
        
    _log_and_print(include_stdout, "DEBUG", "Passed drive size check.")

        
def check_bandwidth(include_stdout=False):
    """
    ensure download and upload speeds meet minimum requirements
    """
    min_download = 20
    min_upload = 1
    command_output = subprocess.check_output(["speedtest-cli", "--simple"]).decode("utf-8")
    _, download_line, upload_line = command_output.splitlines()
    download_match = re.search("Download: [+-]?([0-9]*[.])?[0-9]+", download_line).group(0)
    download = float(download_match.split()[-1])
    upload_match = re.search("Upload: [+-]?([0-9]*[.])?[0-9]+", upload_line).group(0)
    upload = float(upload_match.split()[-1])

    if download < min_download:
        _log_and_print(include_stdout, "INFO", f"Please ensure download speed is at least {min_download} mbps.")
    if upload < min_upload:
        _log_and_print(include_stdout, "INFO", f"Please ensure upload speed is at least {min_upload} mbps.")

    _log_and_print(include_stdout, "DEBUG", "Passed bandwidth check.")


def _check_pcie(selected_gpu):
    """
    ensure gpu pcie resources pass minimum benchmark
    return generation and width
    """
    command_output = os.popen(f"nvidia-smi -q -i {selected_gpu}").read()
    command_output = command_output.splitlines()
    pci_generation = None
    pci_width = None
    for i, line in enumerate(command_output):
        if "GPU Link Info" in line and (i + 6) < len(command_output):
            pci_generation = command_output[i + 3].split(":")[-1]
            pci_width = command_output[i + 6].split(":")[-1]
            break

    if pci_generation and pci_width:
        pci_generation = int(pci_generation)
        pci_width = int(pci_width.replace("x", ""))

    return pci_generation, pci_width


def check_gpu_resources(include_stdout=False):
    """
    ensure gpu resources are present and minimum PCIe requirements are met
    benchmarking does not happen here, this is just a quick check for expected GPUs and PCIe
    """
    gpu_info = run_shell_cmd("nvidia-smi --query-gpu=index,gpu_name --format=csv", format_output=False, quiet=True).splitlines()
    if len(gpu_info) < 2:
        _log_and_print(include_stdout, "INFO", f"Please ensure your machine has at least one compatible GPU.")
        
    gpu_to_result = {}
    for gpu in gpus:
        gpu_str = str(gpu)
        gpu_to_result[gpu_str] = True
        pci_generation, pci_width = _check_pcie(gpu)
        min_generation = 3
        min_width = 1
        if not pci_generation or pci_generation < min_generation or not pci_width or pci_width < min_width:
            _log_and_print(include_stdout, "INFO", f"Please ensure GPU at index {gpu} is at least gen {min_generation} PCIe with at least {min_width}x width.")
            gpu_to_result[gpu_str] = False

    if any(gpu_to_result.values()):
        _log_and_print(include_stdout, "DEBUG", "Passed GPU resources check with at least one GPU.")
    

def check_cpu_resources(include_stdout=False):
    """
    ensure cpu resources meet minimum requirements
    """
    min_cpus = 2.0
    command_output = subprocess.check_output(["nproc"]).decode("utf-8")
    cpus = float(command_output)
    if cpus < min_cpus:
        _log_and_print(include_stdout, "INFO", f"Please ensure your machine has at least {min_cpus} CPUs.")

    _log_and_print(include_stdout, "DEBUG", "Passed CPU resources check.")


def check_memory_resources(include_stdout=False):
    """
    ensure system has enough ram for rentaflop
    """
    min_ram = 8.0
    command_output = subprocess.check_output(["free", "--giga"]).decode("utf-8")
    command_output = command_output.splitlines()
    ram = float(command_output[1].split()[1])
    if ram < min_ram:
        _log_and_print(include_stdout, "INFO", f"Please ensure your machine has at least {min_ram} GB of RAM.")

    _log_and_print(include_stdout, "DEBUG", "Passed memory resources check.")


def perform_host_requirement_checks():
    """
    performs all the necessary checks to ensure host can be added to rentaflop network
    """
    check_p2p(include_stdout=True)
    check_drive_size(include_stdout=True)
    check_bandwidth(include_stdout=True)
    check_gpu_resources(include_stdout=True)
    check_cpu_resources(include_stdout=True)
    check_memory_resources(include_stdout=True)
