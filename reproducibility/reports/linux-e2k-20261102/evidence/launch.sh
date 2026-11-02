#!/bin/sh
cd /home/user/mqtty-repro-20261102-e2k-a20f9531 || exit 1
python3 -u reproducibility/run_prebuilt.py --app app/EntityFX.MqttY.MqttRelay.App.dll --profile profiles/broker-benchmark.v2.json --baseline reproducibility/baseline-windows-smoke.json --output results > run.log 2>&1
code=$?
printf '%s\n' "$code" > exit-code.txt
exit "$code"
