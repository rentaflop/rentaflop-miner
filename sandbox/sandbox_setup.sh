#!/bin/bash
sudo ldconfig.real
sed -i -e "s/rentaflop_hostname/$HOSTNAME/g" config.json
sed -i -e "s/wallet_address/$WALLET_ADDRESS/g" config.json
sed -i -e "s/mining_algorithm/$MINING_ALGORITHM/g" config.json
# must escape any forward slashes in url for sed replacement to work
POOL_URL_ESCAPED=$(printf '%s\n' "$POOL_URL" | sed -e 's/[\/&]/\\&/g')
sed -i -e "s/pool_url/$POOL_URL_ESCAPED/g" config.json
mv config.json trex
python3 sandbox_queue.py
