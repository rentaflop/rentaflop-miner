# to be run when first installing rentaflop miner
# installs requirements then starts daemon, which handles all the rest
cd ~/rentaflop-host
apt-get update && apt-get install python3-pip -y
pip3 install -r requirements.txt
python3 daemon.py &
