#!/bin/bash
source /opt/miniconda/etc/profile.d/conda.sh
conda activate passivbot-env
python /opt/passivbot/passivbot.py binance_01 FETUSDT /opt/passivbot/configs/live/FETUSDT.json --leverage 20
