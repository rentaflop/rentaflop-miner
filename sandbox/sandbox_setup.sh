#!/bin/bash
sudo ldconfig.real
timeout $TIMEOUT python3 sandbox_queue.py
