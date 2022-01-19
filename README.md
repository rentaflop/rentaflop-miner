# rentaflop-host

This repo contains the host software for [rentaflop](https://rentaflop.com), the crowdsourced cloud provider.

## about
The host software controls all actions run by rentaflop hosts. In particular, it contains host hardware registration,
a daemon that communicates with rentaflop's servers for instructions, and functionality to launch crypto mining and
guest sandbox connections. We've open-sourced our host software in the interest of security. Security is rentaflop's
priority, therefore commands run on the host machine (either by the host or guests) must be carried out safely.

If you spot a vulnerability, or something that could be improved, please create a pull request or contact support@rentaflop.com.

## structure