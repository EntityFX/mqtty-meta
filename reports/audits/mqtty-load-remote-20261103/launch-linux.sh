#!/bin/bash
set -eu
export DOTNET_ROOT=/home/user/runtimes/dotnet6-6.0.10.11/dotnet
export PATH="$DOTNET_ROOT:$PATH"
export PYTHONIOENCODING=utf-8
cd /home/user/mqtty-load-benchmark-20261103-001
exec /bin/python3 -u controller/remote_load_campaign.py --root /home/user/mqtty-load-benchmark-20261103-001 --main-wall-limit 28800 --pilot-wall-limit 5400 > remote-stdout.log 2> remote-stderr.log
