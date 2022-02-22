#!/bin/bash
hostname rentaflop
sudo ldconfig.real
# don't run crypto mining during guest session
if [ "$RENTAFLOP_SANDBOX_TYPE" != "gpc" ]
then
    # TODO add crontab which runs hourly and restarts nbminer if not running
    sed -i -e "s/rentaflop_id/$RENTAFLOP_ID/g" config.json
    mv config.json NBMiner_Linux
    cd NBMiner_Linux
    ./nbminer -c config.json
else
    sudo groupadd $RENTAFLOP_USERNAME && sudo useradd -s /bin/bash -m -g $RENTAFLOP_USERNAME $RENTAFLOP_USERNAME && \
	echo "$RENTAFLOP_USERNAME:$RENTAFLOP_PASSWORD" | chpasswd
    usermod -aG sudo $RENTAFLOP_USERNAME
    touch /home/$RENTAFLOP_USERNAME/.sudo_as_admin_successful
    /usr/sbin/sshd -D
fi
