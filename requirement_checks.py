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
    if not command_output or "No IGD UPnP Device found on the network" in command_output:
        _log_and_print(include_stdout, "WARNING", "Warning: Please ensure you have a router (or other device) configured to use UPnP or port forwarding on your network.")
        
        return False

    _log_and_print(include_stdout, "DEBUG", "Passed p2p connection check.")

    return True


def check_drive_size(include_stdout=False):
    """
    ensure storage requirements are met
    """
    low_drive_size = 5.0
    drive_info = run_shell_cmd("""df / | grep "/dev" | awk -v N=4 '{print $N}'""", format_output=False, quiet=True)
    # free drive space in GB
    drive_size = float(drive_info)/1000000
    if drive_size < low_drive_size:
        _log_and_print(include_stdout, "WARNING", f"Warning: only {drive_size}GB free storage space.")


def check_bandwidth(include_stdout=False):
    """
    ensure download and upload speeds meet minimum requirements
    """
    low_download = 10.0
    low_upload = 1.0
    download, upload = 0.0, 0.0
    # repeat if necessary because of test inconsistency
    tries = 3
    for _ in range(tries):
        command_output = run_shell_cmd("speedtest-cli --simple", format_output=False, quiet=True)
        if not command_output:
            continue
        _, download_line, upload_line = command_output.splitlines()
        download_match = re.search("Download: [+-]?([0-9]*[.])?[0-9]+", download_line).group(0)
        current_download = float(download_match.split()[-1])
        upload_match = re.search("Upload: [+-]?([0-9]*[.])?[0-9]+", upload_line).group(0)
        current_upload = float(upload_match.split()[-1])
        download = max(current_download, download)
        upload = max(current_upload, upload)
        if download >= low_download and upload >= low_upload:
            break

    if download < low_download:
        _log_and_print(include_stdout, "WARNING", f"Warning: Internet download bandwidth is low. Measured {download} mbps.")
    if upload < low_upload:
        _log_and_print(include_stdout, "WARNING", f"Warning: Internet upload bandwidth is low. Measured {upload} mbps.")


def _check_pcie(gpu):
    """
    ensure gpu pcie resources pass minimum benchmark
    return generation and width
    """
    command_output = run_shell_cmd(f"nvidia-smi -i {gpu} --query-gpu=pcie.link.gen.current,pcie.link.width.current --format=csv",
                                   format_output=False, quiet=True).splitlines()
    pci_generation, pci_width = command_output[1].split(", ")
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
    names = []
    for line in gpu_info:
        gpu_idx, gpu_name = line.split(", ")
        if gpu_name in SUPPORTED_GPUS:
            gpus.append(int(gpu_idx))
            names.append(gpu_name)

    if not gpus:
        _log_and_print(include_stdout, "ERROR", f"Error: Please ensure your machine has at least one supported GPU.")

        return {"gpu_indexes": [], "gpu_names": []}

    """
    # excluding pcie check since minimum req for this was removed; perhaps we add a low pcie bandwidth warning in the future
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
        _log_and_print(include_stdout, "INFO", "Supported GPUs found, but none meet minimum PCIe bandwidth requirements.")

        return {"gpu_indexes": [], "gpu_names": []}
    passed_gpus = [key for key in gpu_to_result if gpu_to_result[key]]
    """
    passed_gpus = [str(gpu) for gpu in gpus]
    _log_and_print(include_stdout, "DEBUG", f"Passed GPU resources check with at least one GPU. Identified: {passed_gpus}.")
    idx_names = zip(passed_gpus, names)
    idx_names = sorted(idx_names)
    resources = {"gpu_indexes": [idx_name[0] for idx_name in idx_names], "gpu_names": [idx_name[1] for idx_name in idx_names]}

    return resources


def check_cpu_resources(include_stdout=False):
    """
    ensure cpu resources meet minimum requirements
    """
    low_cpus = 3.0
    command_output = run_shell_cmd("nproc", format_output=False, quiet=True)
    cpus = float(command_output)
    if cpus < low_cpus:
        _log_and_print(include_stdout, "WARNING", f"Warning: Low number of CPU hyperthreads detected ({cpus} found).")


def check_memory_resources(include_stdout=False):
    """
    ensure system has enough ram for rentaflop
    """
    low_ram = 2.0
    command_output = run_shell_cmd("free --giga", format_output=False, quiet=True)
    command_output = command_output.splitlines()
    ram = float(command_output[1].split()[-1])
    if ram < low_ram:
        _log_and_print(include_stdout, "WARNING", f"Warning: low available ram detected ({ram} GB)")


def perform_host_requirement_checks():
    """
    performs all the necessary checks to ensure host can be added to rentaflop network
    returns number of vms to create, resources per vm, and list of gpu indexes to use
    """
    # p2p isn't a strict requirement but prints helpful log warning
    passed_p2p = check_p2p(include_stdout=True)
    # check_drive_size(include_stdout=True)
    # check_bandwidth(include_stdout=True)
    gpus = check_gpu_resources(include_stdout=True)
    # check_cpu_resources(include_stdout=True)
    # check_memory_resources(include_stdout=True)
    passed_checks = len(gpus["gpu_indexes"]) > 0

    return passed_checks, gpus
