# rentaflop-miner (beta)

Rentaflop miner is the cryptocurrency mining software for [rentaflop](https://rentaflop.com), the crowdsourced cloud provider.
Rentaflop is the best way to earn passive income from your Nvidia GPU while you’re not using it. We mine crypto and solve
3D graphics or Artificial Intelligence tasks for others on your device, putting your hardware to work helping others while
you earn crypto.

## installation

To install and run rentaflop miner, please follow the instructions [here](https://portal.rentaflop.com/blog/hosting).

## system requirements

There are only a few hard requirements for running rentaflop miner.     
Here are the minimum basic system requirements:

1. A discrete Nvidia graphics card from the last several years (10, 20, or 30 series)
1. About 3GB free storage
1. An Internet connection with the ability to run Universal Plug and Play (UPnP)
1. HiveOS     

Most consumer routers support UPnP out of the box. If you're not currently running HiveOS, we recommend getting an
inexpensive 8GB flash drive and following the GPU instructions [here](https://hiveon.com/install/) to create a bootable USB. We like this route because
it allows you to separate mining from personal business. You can boot the USB when you want to mine and boot your other drive when you want
to do gaming or use your graphics card(s) otherwise.
Here are the specific graphics cards we currently support: NVIDIA GeForce GTX 1070 Ti, NVIDIA GeForce GTX 1080, NVIDIA GeForce GTX 1080 Ti,
NVIDIA GeForce RTX 2070 Super, NVIDIA GeForce RTX 2080, NVIDIA GeForce RTX 2080 Super, NVIDIA GeForce RTX 2080 Ti, NVIDIA GeForce RTX 3050,
NVIDIA GeForce RTX 3060, NVIDIA GeForce RTX 3060 Ti, NVIDIA GeForce RTX 3070, NVIDIA GeForce RTX 3070 Ti, NVIDIA GeForce RTX 3080, NVIDIA GeForce RTX 3080 Ti,
NVIDIA GeForce RTX 3090. Our roadmap includes expanding these to
other Nvidia as well as AMD cards.

## about this codebase

This software controls all actions run by rentaflop hosts, any computer running rentaflop miner. In particular, it contains host hardware registration,
a daemon that communicates with rentaflop's servers for instructions, and functionality to launch crypto mining and
user tasks in a secure sandbox. We've open sourced our host software in the interest of security and openness. Security is rentaflop's
priority, therefore commands run on the host machine must be carried out safely.

If you spot a vulnerability, or something that could be improved, please create a pull request or contact [support@rentaflop.com](mailto:support@rentaflop.com).

## structure

```daemon.py```

This is the driver for rentaflop miner. It runs a webserver to listen for commands from rentaflop servers and then executes them.

```utils.py```

Contains useful functionality and utilities that are often used by multiple modules.

```config.py```

Houses some important global variables, mostly used for startup.

```run.sh```

Installs dependencies and runs rentaflop miner.

```requirement_checks```

Checks basic system requirements for running rentaflop miner.

```test/```

Contains test cases and code to make test command queries to the daemon.

```sandbox/```

Code for the sandbox, which runs the crypto miner as well as the task miner. These are both run inside a docker container
for added modularity/security.

## limitations

Commercial use of this software is prohibited. This software may be used, distributed, or modified for any purpose
other than commercial use at the user's risk. In no event shall the authors, copyright holders, or Rentaflop, Inc. be
liable for any claim, damages or other liability, whether in an action of contract, tort or otherwise, arising from,
out of or in connection with the software or the use or other dealings in the software. Rentaflop, Inc. makes no
representations or warranties, express or implied, regarding the nature or quality of this software.
