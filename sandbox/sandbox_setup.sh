#!/bin/bash
# don't run crypto mining during guest session
if [ "$RENTAFLOP_SANDBOX_TYPE" != "gpc" ]
then
    sed -i -e "s/rentaflop_id/$RENTAFLOP_ID/g" config.json
    mv config.json NBMiner_Linux
    cd NBMiner_Linux
    ./nbminer -c config.json &    
fi
sudo ldconfig.real
/usr/sbin/sshd -D
