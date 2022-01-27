# to be run when first installing rentaflop miner
# installs requirements then starts daemon, which handles all the rest
pip3 install -r requirements.txt
python3 daemon.py &
