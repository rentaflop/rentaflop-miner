# rentaflop-host

This repo contains the host software for [rentaflop](https://rentaflop.com), the crowdsourced cloud provider.

## limitations
Commercial use of this software is prohibited. This software may be used, distributed, or modified for any purpose
other than commercial use at the user's risk. In no event shall the authors, copyright holders, or Rentaflop, INC. be
liable for any claim, damages or other liability, whether in an action of contract, tort or otherwise, arising from,
out of or in connection with the software or the use or other dealings in the software. Rentaflop, INC. makes no
representations or warranties, express or implied, regarding the nature or quality of this software.

## about
The host software controls all actions run by rentaflop hosts. In particular, it contains host hardware registration,
a daemon that communicates with rentaflop's servers for instructions, and functionality to launch crypto mining and
guest sandbox connections. We've open sourced our host software in the interest of security. Security is rentaflop's
priority, therefore commands run on the host machine (either by the host or guests) must be carried out safely.

If you spot a vulnerability, or something that could be improved, please create a pull request or contact support@rentaflop.com.

## installation
To install rentaflop miner, run the following command:
```
git clone https://github.com/rentaflop/rentaflop-host.git && ./rentaflop-host/run.sh
```

## structure