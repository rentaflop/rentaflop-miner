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
    # TODO don't need rentaflop size since rentaflop is already fully installed prior to when this gets run
    min_rentaflop_size = 10.0
    min_per_vm_size = 15.0
    drive_info = run_shell_cmd("""df / | grep "/dev" | awk -v N=4 '{print $N}'""", format_output=False, quiet=True)
    # TODO run disk-expand utility from hive (or better, make it work on all linux distros)
    # free drive space in GB
    drive_size = float(drive_info)/1000000
    if drive_size < min_rentaflop_size+min_per_vm_size:
        _log_and_print(include_stdout, "INFO", f"Please ensure storage drive is at least {min_rentaflop_size} GB plus {min_per_vm_size} per GPU.")

        return 0, 0
        
    _log_and_print(include_stdout, "DEBUG", "Passed drive size check.")
    n_vms_possible = (drive_size-min_rentaflop_size)//min_per_vm_size
    
    return n_vms_possible, drive_size


def check_bandwidth(include_stdout=False):
    """
    ensure download and upload speeds meet minimum requirements
    """
    # TODO at some point, figure out whether there are other hosts at this ip that split the bandwidth
    min_download_per_vm = 10
    # TODO upload bandwidth per vm at some point?
    min_upload = 5
    download, upload = 0, 0
    # repeat because of inconsistency
    tries = 3
    for _ in range(tries):
        command_output = run_shell_cmd("speedtest-cli --simple", format_output=False, quiet=True)
        _, download_line, upload_line = command_output.splitlines()
        download_match = re.search("Download: [+-]?([0-9]*[.])?[0-9]+", download_line).group(0)
        current_download = float(download_match.split()[-1])
        upload_match = re.search("Upload: [+-]?([0-9]*[.])?[0-9]+", upload_line).group(0)
        current_upload = float(upload_match.split()[-1])
        download = max(current_download, download)
        upload = max(current_upload, upload)

    if download < min_download_per_vm:
        _log_and_print(include_stdout, "INFO", f"Please ensure download speed is at least {min_download_per_vm} mbps per GPU.")
        
        return 0, 0
    if upload < min_upload:
        _log_and_print(include_stdout, "INFO", f"Please ensure upload speed is at least {min_upload} mbps.")

        return 0, 0

    _log_and_print(include_stdout, "DEBUG", "Passed bandwidth check.")
    n_vms_possible = download//min_download_per_vm
    
    return n_vms_possible, download


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
    for line in gpu_info:
        gpu_idx, gpu_name = line.split(", ")
        if gpu_name in SUPPORTED_GPUS:
            gpus.append(int(gpu_idx))

    if not gpus:
        _log_and_print(include_stdout, "INFO", f"Please ensure your machine has at least one supported GPU.")

        return 0, []
    
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

        return 0, []

    _log_and_print(include_stdout, "DEBUG", "Passed GPU resources check with at least one GPU.")
    passed_gpus = [key for key in gpu_to_result if gpu_to_result[key]]

    return len(passed_gpus), sorted(passed_gpus)
        

def check_cpu_resources(include_stdout=False):
    """
    ensure cpu resources meet minimum requirements
    """
    min_cpus_per_vm = 2.0
    command_output = run_shell_cmd("nproc", format_output=False, quiet=True)
    cpus = float(command_output)
    if cpus < min_cpus_per_vm:
        _log_and_print(include_stdout, "INFO", f"Please ensure your machine has at least {min_cpus_per_vm} CPU threads per GPU.")

        return 0, 0
    
    _log_and_print(include_stdout, "DEBUG", "Passed CPU resources check.")
    n_vms_possible = cpus//min_cpus_per_vm
    
    return n_vms_possible, cpus


def check_memory_resources(include_stdout=False):
    """
    ensure system has enough ram for rentaflop
    """
    min_ram_per_vm = 8.0
    command_output = run_shell_cmd("free --giga", format_output=False, quiet=True)
    command_output = command_output.splitlines()
    ram = float(command_output[1].split()[1])
    if ram < min_ram_per_vm:
        _log_and_print(include_stdout, "INFO", f"Please ensure your machine has at least {min_ram_per_vm} GB of RAM per GPU.")

        return 0, 0
    
    _log_and_print(include_stdout, "DEBUG", "Passed memory resources check.")
    n_vms_possible = ram//min_ram_per_vm
    
    return n_vms_possible, ram


def perform_host_requirement_checks():
    """
    performs all the necessary checks to ensure host can be added to rentaflop network
    returns number of vms to create, resources per vm, and list of gpu indexes to use
    """
    passed_p2p = check_p2p(include_stdout=True)
    n_vms_storage, storage = check_drive_size(include_stdout=True)
    n_vms_download, download = check_bandwidth(include_stdout=True)
    n_vms_gpu, gpus = check_gpu_resources(include_stdout=True)
    n_vms_cpu, cpus = check_cpu_resources(include_stdout=True)
    n_vms_ram, ram = check_memory_resources(include_stdout=True)
    n_vms, vm_storage, vm_download, vm_cpus, vm_ram = [0]*5
    if passed_p2p:
        n_vms = float(min(n_vms_storage, n_vms_download, n_vms_gpu, n_vms_cpu, n_vms_ram))
        vm_storage = storage/n_vms
        vm_download = download/n_vms
        vm_cpus = cpus/n_vms
        vm_ram = ram/n_vms
    # if more available gpus than vms, select gpus to use based on first n_vms indexes
    # TODO do better than first n_vms selection
    if n_vms_gpu > n_vms:
        gpus = gpus[:int(n_vms)]

    return n_vms, vm_storage, vm_download, vm_cpus, vm_ram, gpus
