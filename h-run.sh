[[ `ps aux | grep "daemon.py" | grep -v grep | wc -l` != 0 ]] &&
    echo -e "${RED}$MINER_NAME miner is already running${NOCOLOR}" &&
    exit 1

cd $MINER_DIR
python3 daemon.py
