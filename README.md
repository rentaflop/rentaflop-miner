# rentaflop-miner

Rentaflop miner is the cryptocurrency mining software for [rentaflop](https://rentaflop.com), the crowdsourced cloud provider.
Rentaflop is the best way to earn passive income from your NVIDIA GPU while you’re not using it. We mine crypto and solve
AI calculations for others on your device, earning you more crypto without having to lift a finger.

## limitations
Commercial use of this software is prohibited. This software may be used, distributed, or modified for any purpose
other than commercial use at the user's risk. In no event shall the authors, copyright holders, or Rentaflop, Inc. be
liable for any claim, damages or other liability, whether in an action of contract, tort or otherwise, arising from,
out of or in connection with the software or the use or other dealings in the software. Rentaflop, Inc. makes no
representations or warranties, express or implied, regarding the nature or quality of this software.

## about
This software controls all actions run by rentaflop hosts. In particular, it contains host hardware registration,
a daemon that communicates with rentaflop's servers for instructions, and functionality to launch crypto mining and
guest sandbox connections. We've open sourced our host software in the interest of security. Security is rentaflop's
priority, therefore commands run on the host machine (either by the host or guests) must be carried out safely.

If you spot a vulnerability, or something that could be improved, please create a pull request or contact support@rentaflop.com.

## installation
To install rentaflop miner, run the following command:
```
git clone https://github.com/rentaflop/rentaflop-miner.git && ./rentaflop-miner/run.sh
```

## structure