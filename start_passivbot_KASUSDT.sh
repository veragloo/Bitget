#!/bin/bash
source /opt/miniconda/etc/profile.d/conda.sh
conda activate passivbot-env
python /opt/passivbot/passivbot.py binance_01 KASUSDT /opt/passivbot/configs/live/KASUSDT.json --leverage 20
