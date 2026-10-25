# Monitoring Script Updates - 2026-10-24

## Summary of Changes

### New Features Added to `monitor_sensitivity.py`

1. **hd250 Mount Status Check**:
   - Added `check_hd250_mount()` function
   - Checks if `/run/media/entityfx/hd250` is properly mounted
   - Displays device name, mount point, and filesystem type
   - Shows error message if mount fails

2. **QoS1 Diagnostics Service Monitoring**:
   - Added `qos1_service_status()` function
   - Checks status of `mqtty-qos1-diagnostics-v1.service`
   - Displays MainPID, ActiveState, SubState, Result, and ExecMainStatus
   - Added `qos1_progress_status()` function
   - Reads and displays progress from `/run/media/entityfx/hd250/mqtty-evidence/qos1-linux-diagnostics-v1/progress.json`

3. **Web Service Health Check**:
   - Added `web_health_check()` function
   - Queries Web service health endpoint at `http://127.0.0.1:5088/healthz`
   - Displays status, PID, memory usage, running jobs, and queued jobs
   - Shows error information if health check fails

### Display Enhancements

All new monitoring information is displayed in the terminal with color-coded status:
- Green for normal/successful states
- Red for errors/issues
- Yellow for warnings

### Constants Added

- `QOS1_PROGRESS`: Path to QoS1 diagnostics progress file
- `WEB_HEALTH_ENDPOINT`: URL for Web service health checks

### Integration

The new monitoring functions are integrated into the main loop of `monitor_sensitivity.py` and execute automatically on each refresh cycle (every 5 seconds by default).

## Usage

The script continues to work as before:
```bash
python3 tools/status/monitor_sensitivity.py
```

All new monitoring features are automatic and require no additional configuration.
