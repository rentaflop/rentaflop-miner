[[ `ps aux | grep "daemon.py" | grep -v grep | wc -l` != 0 ]] &&
    echo -e "${RED}$CUSTOM_NAME is already running${NOCOLOR}" &&
    exit 1

cd $MINER_DIR/$CUSTOM_MINER
# if daemon log exists, it's been run before and reqs are installed so we run normally
if [[ -f "daemon.log" ]]; then
    python3 daemon.py
else
    # otherwise we need to run installation
    ./"run.sh"
fi
