#!/bin/bash
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
    user_home=/home/$RENTAFLOP_USERNAME
    touch $user_home/.sudo_as_admin_successful
    cd $user_home
    sudo -H -u $RENTAFLOP_USERNAME bash -c 'jupyter notebook --generate-config'
    jupyter_passwd_hash=$(python3 -c "from notebook.auth import passwd; print(passwd('$RENTAFLOP_PASSWORD'))")
    sudo -H -u $RENTAFLOP_USERNAME bash -c "openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout .jupyter/jupyter.key -out .jupyter/jupyter.pem -subj '/C=US/ST=Virginia/L=Arlington/O=Rentaflop, Inc.'"
    echo "c.NotebookApp.password='$jupyter_passwd_hash'" >> .jupyter/jupyter_notebook_config.py
    echo "c.NotebookApp.certfile = u'$user_home/.jupyter/jupyter.pem'" >> .jupyter/jupyter_notebook_config.py
    echo "c.NotebookApp.keyfile = u'$user_home/.jupyter/jupyter.key'" >> .jupyter/jupyter_notebook_config.py
    echo "c.NotebookApp.open_browser = False" >> .jupyter/jupyter_notebook_config.py
    echo "c.NotebookApp.ip = '*'" >> .jupyter/jupyter_notebook_config.py
    sudo -H -u $RENTAFLOP_USERNAME bash -c 'jupyter notebook &'
    /usr/sbin/sshd -D    
fi
