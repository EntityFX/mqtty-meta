# Broker stand

Run the PowerShell controller against a Linux Docker context with cgroup v2. All broker traffic uses direct host networking. `singleCorePinned` selects one CPU; `hostAllCores` requires an empty CPU set. Both modes enforce 4 GiB of container memory. In `singleHostSequential`, start removes other containers carrying the stand label before loading the selected broker.

From the MqttBenchmark directory:

```powershell
$inventory = 'config/broker-stand.v1.json'
$output = 'artifacts/broker-stand/run-001'
./scripts/BrokerStand.ps1 -Action pull -ConfigPath $inventory -OutputDirectory $output
./scripts/BrokerStand.ps1 -Action start -ConfigPath $inventory -OutputDirectory $output
./scripts/BrokerStand.ps1 -Action health -ConfigPath $inventory -OutputDirectory $output
./scripts/BrokerStand.ps1 -Action capture -CaptureSeconds 10 -ConfigPath $inventory -OutputDirectory $output
./scripts/BrokerStand.ps1 -Action stop -ConfigPath $inventory -OutputDirectory $output
./scripts/BrokerStand.ps1 -Action reset -ConfigPath $inventory -OutputDirectory $output
```

Set `dockerContext`, `controlInterface`, MQTT/management URIs, `activeBroker`, and its `loaded` flag in the inventory for the target host. Distributed inventories select the active broker's own `dockerContext` and use MQTT port 1883. ActiveMQ's MQTT and HTTP listeners bind the control address. EMQX's dashboard, Erlang distribution, and internal RPC listeners also bind that address.

`pull` builds Aedes and ActiveMQ and records local image IDs and build-input hashes under `build/`. Keep custom `digest` fields null: local image IDs are not registry manifest digests. Deployment, health, and capture verify this provenance, including current source hashes. Stop/reset remain available after source changes. Registry images require verified SHA-256 digests.

Builds may optionally use `MQTTY_BUILD_HTTP_PROXY` and `MQTTY_BUILD_NO_PROXY` from the controller process environment. Both custom builds use host networking. Compose stores environment references, not proxy values, and Docker's predefined proxy build arguments are not added to the runtime image. Direct internet access needs no proxy setting.

The Compose document uses JSON syntax, which is valid YAML. The controller selects a single service and transfers the exact effective configuration through encoded environment values and a container entrypoint. There are no host bind mounts, including when the client is Windows and the daemon is remote Linux.

Choose a fresh output directory for each campaign run. Start saves the effective Compose/config files and `run-provenance.json`; capture verifies their deployed hashes without regenerating them. Telemetry appends NDJSON samples. A single container command schedules samples against `/proc/uptime` deadlines, independent of Docker/SSH round-trip time. CPU/throttle, memory, and disk I/O are container cgroup counters; RSS covers broker processes. Network and TCP tables are explicitly host-shared. `versions.json` identifies the running image and reports the actual broker and Node/Java runtime versions.

Application logging is warning/error to console, with bounded Docker local logs. ActiveMQ's Log4j and Java logging are configured together and its distribution JVM management agent is disabled. One-time distribution startup banners may still appear.
