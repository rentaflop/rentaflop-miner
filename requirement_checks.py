"""
performs minimum system requirement checks to host on rentaflop
checks drive size, hardware virtualization, firewall support for UPnP, internet bandwidth, CPU, etc.
if no GPU, instruct user to drive to micro center
"""
import re
from config import DAEMON_LOGGER
from utils import run_shell_cmd, SUPPORTED_GPUS


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
    command_output = run_shell_cmd("upnpc -s", format_output=False, quiet=True)
    if "No IGD UPnP Device found on the network" in command_output:
        _log_and_print(include_stdout, "INFO", "Please ensure you have a router (or other device) configured to use UPnP on your network.")

        return False

    _log_and_print(include_stdout, "DEBUG", "Passed p2p connection check.")

    return True


def check_drive_size(include_stdout=False):
    """
    ensure storage requirements are met
    """
    min_drive_size = 25.0
    drive_info = run_shell_cmd("fdisk -l", format_output=False, quiet=True)
    # TODO run disk-expand utility from hive (or better, make it work on all linux distros)
    match = re.search("Disk /dev/sda: [+-]?([0-9]*[.])?[0-9]+", drive_info).group(0)
    drive_size = float(match.split()[-1])
    if drive_size < min_drive_size:
        _log_and_print(include_stdout, "INFO", f"Please ensure drive is at least {min_drive_size} GB.")

        return False
        
    _log_and_print(include_stdout, "DEBUG", "Passed drive size check.")

    return True


def check_bandwidth(include_stdout=False):
    """
    ensure download and upload speeds meet minimum requirements
    """
    min_download = 20
    min_upload = 1
    command_output = run_shell_cmd("speedtest-cli --simple", format_output=False, quiet=True)
    _, download_line, upload_line = command_output.splitlines()
    download_match = re.search("Download: [+-]?([0-9]*[.])?[0-9]+", download_line).group(0)
    download = float(download_match.split()[-1])
    upload_match = re.search("Upload: [+-]?([0-9]*[.])?[0-9]+", upload_line).group(0)
    upload = float(upload_match.split()[-1])

    if download < min_download:
        _log_and_print(include_stdout, "INFO", f"Please ensure download speed is at least {min_download} mbps.")
        
        return False
    if upload < min_upload:
        _log_and_print(include_stdout, "INFO", f"Please ensure upload speed is at least {min_upload} mbps.")

        return False

    _log_and_print(include_stdout, "DEBUG", "Passed bandwidth check.")

    return True


def _check_pcie(gpu):
    """
    ensure gpu pcie resources pass minimum benchmark
    return generation and width
    """
    command_output = run_shell_cmd("nvidia-smi -i {gpu} --query-gpu=pcie.link.gen.current,pcie.link.width.current --format=csv",
                                   format_output=False, quiet=True).splitlines()
    pci_generation, pci_width = command_output.split(", ")
    pci_generation = int(pci_generation)
    pci_width = int(pci_width)

    return pci_generation, pci_width


def check_gpu_resources(include_stdout=False):
    """
    ensure gpu resources are present and minimum PCIe requirements are met
    benchmarking does not happen here, this is just a quick check for expected GPUs and PCIe
    """
    gpu_info = run_shell_cmd("nvidia-smi --query-gpu=index,gpu_name --format=csv", format_output=False, quiet=True).splitlines()
    if len(gpu_info) > 1:
        gpu_info = gpu_info[1:]
    gpus = []
    for line in gpu_info:
        gpu_idx, gpu_name = line.split(", ")
        if gpu_name in SUPPORTED_GPUS:
            gpus.append(int(gpu_idx))

    if not gpus:
        _log_and_print(include_stdout, "INFO", f"Please ensure your machine has at least one compatible GPU.")

        return False, {}
    
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

    if not any(gpu_to_result.values()):
        _log_and_print(include_stdout, "INFO", "No valid GPUs found.")

        return False, gpu_to_result

    _log_and_print(include_stdout, "DEBUG", "Passed GPU resources check with at least one GPU.")

    return True, gpu_to_result
        

def check_cpu_resources(include_stdout=False):
    """
    ensure cpu resources meet minimum requirements
    """
    min_cpus = 2.0
    command_output = run_shell_cmd("nproc", format_output=False, quiet=True)
    cpus = float(command_output)
    if cpus < min_cpus:
        _log_and_print(include_stdout, "INFO", f"Please ensure your machine has at least {min_cpus} CPUs.")

        return False
    
    _log_and_print(include_stdout, "DEBUG", "Passed CPU resources check.")

    return True


def check_memory_resources(include_stdout=False):
    """
    ensure system has enough ram for rentaflop
    """
    min_ram = 8.0
    command_output = run_shell_cmd("free --giga", format_output=False, quiet=True)
    command_output = command_output.splitlines()
    ram = float(command_output[1].split()[1])
    if ram < min_ram:
        _log_and_print(include_stdout, "INFO", f"Please ensure your machine has at least {min_ram} GB of RAM.")

        return False
    
    _log_and_print(include_stdout, "DEBUG", "Passed memory resources check.")

    return True


def perform_host_requirement_checks():
    """
    performs all the necessary checks to ensure host can be added to rentaflop network
    """
    passed_gpu, gpus = check_gpu_resources(include_stdout=True)
    passed_p2p = check_p2p(include_stdout=True)
    passed_storage = check_drive_size(include_stdout=True)
    passed_bandwidth = check_bandwidth(include_stdout=True)
    passed_cpu = check_cpu_resources(include_stdout=True)
    passed_memory = check_memory_resources(include_stdout=True)
    passed_all_checks = passed_p2p and passed_storage and passed_bandwidth and passed_gpu and passed_cpu and passed_memory

    return passed_all_checks, gpus
