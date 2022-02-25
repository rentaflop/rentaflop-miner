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
    touch /home/$RENTAFLOP_USERNAME/.sudo_as_admin_successful
    jupyter_passwd_hash=$(python3 -c "from notebook.auth import passwd; print(passwd('$RENTAFLOP_PASSWORD'))")
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout jupyter.key -out jupyter.pem -subj '/C=US/ST=Virginia/L=Arlington/O=Rentaflop, Inc.'
    jupyter notebook --generate-config
    echo "c.NotebookApp.password='$jupyter_passwd_hash'" >> /root/.jupyter/jupyter_notebook_config.py
    echo "c.NotebookApp.certfile = u'/jupyter.pem'" >> /root/.jupyter/jupyter_notebook_config.py
    echo "c.NotebookApp.keyfile = u'/jupyter.key'" >> /root/.jupyter/jupyter_notebook_config.py
    echo "c.NotebookApp.open_browser = False" >> /root/.jupyter/jupyter_notebook_config.py
    echo "c.NotebookApp.ip = '*'" >> /root/.jupyter/jupyter_notebook_config.py
    jupyter notebook &
    /usr/sbin/sshd -D
fi
