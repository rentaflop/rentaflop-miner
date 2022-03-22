#!/bin/bash
sudo ldconfig.real
sed -i -e "s/rentaflop_hostname/$HOSTNAME/g" config.json
sed -i -e "s/wallet_address/$WALLET_ADDRESS/g" config.json
mv config.json NBMiner_Linux
python3 sandbox_queue.py
