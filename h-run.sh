[[ `ps aux | grep "daemon.py" | grep -v grep | wc -l` != 0 ]] &&
    echo -e "${RED}$CUSTOM_NAME is already running${NOCOLOR}" &&
    exit 1

# if daemon log exists, it's been run before and reqs are installed so we run normally
if [[ -f "$MINER_DIR/$CUSTOM_MINER/daemon.log" ]]; then
    cd $MINER_DIR
    python3 daemon.py
else
    # otherwise we need to run installation
    ./"$MINER_DIR/$CUSTOM_MINER/run.sh"
fi
