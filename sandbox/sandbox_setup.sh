#!/bin/bash
# don't run crypto mining during guest session
if [ "$RENTAFLOP_SANDBOX_TYPE" != "gpc" ]
then
    mv config.json NBMiner_Linux
    cd NBMiner_Linux
    ./nbminer -c config.json &    
fi
sudo ldconfig.real
/usr/sbin/sshd -D
