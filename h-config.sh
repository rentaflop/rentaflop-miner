# Returns the version which is running now.
function miner_ver() {
    echo $MINER_LATEST_VER
}

# Not required
# Returns configured fork
function miner_fork() {
    echo $MINER_DEFAULT_FORK
}

# Outputs generated config file
function miner_config_echo() {
    export MINER_FORK=`miner_fork`
    local MINER_VER=`miner_ver`
    miner_echo_config_file "$MINER_DIR/$CUSTOM_MINER/rentaflop_config.json"
}

# Generates config file
function miner_config_gen() {
    local MINER_CONFIG="$MINER_DIR/$CUSTOM_MINER/rentaflop_config.json"
    # exit if config already exists
    if [[ -f "$MINER_CONFIG" ]]; then
	exit 0
    fi

    mkfile_from_symlink $MINER_CONFIG
    conf=`echo {\"wallet_address\": \"$RENTAFLOP_MINER_TEMPLATE\"}`

    echo -e "$conf" | jq > $MINER_CONFIG
}
