#!/bin/bash
sudo ldconfig.real
mv config.json NBMiner_Linux
cd NBMiner_Linux
./nbminer -c config.json &
/usr/sbin/sshd -D
