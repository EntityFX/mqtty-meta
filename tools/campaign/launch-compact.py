"""Launch only the owned compact campaign with an independent systemd time limit."""
import datetime
import json
import os
import pathlib
import subprocess
import sys
import uuid
import importlib.util


def timing(now):
    return {'startedAtUtc': now.isoformat(),
            'deadlineUtc': (now + datetime.timedelta(hours=6)).isoformat(),
            'workloadDeadlineUtc': (now + datetime.timedelta(seconds=20100)).isoformat(),
            'runtimeMaxSeconds': 21420, 'stopTimeoutSeconds': 30}


def main():
    import dbus
    root = pathlib.Path(sys.argv[sys.argv.index('--series') + 1]).resolve()
    spec = importlib.util.spec_from_file_location('compact', root / 'source/compact-campaign.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    plan = module.read(root / 'series.json')
    if plan.get('coverageProtocol') != 'compact-v1':
        raise RuntimeError('Only compact-v1 may be launched here')
    for key in ('orchestrationAdapter', 'simulationAdapter'):
        if module.digest(plan[key]) != plan[key + 'Sha256']:
            raise RuntimeError('Frozen orchestration source changed')
    if module.read(root / 'state.json')['status'] != 'prepared':
        raise RuntimeError('Launch requires a prepared series; existing deadline cannot be reset')
    if subprocess.check_output(['findmnt', '-n', '-o', 'TARGET,SOURCE,FSTYPE', '-T', plan['archiveMount']], text=True).split() != [plan['archiveMount'], plan['archiveDevice'], 'ext4']:
        raise RuntimeError('Evidence mount identity changed')
    python = str(pathlib.Path(sys.executable).resolve())
    # The launcher itself uses system Python for D-Bus; the runner needs matplotlib/Markdown.
    python = os.environ['MQB_CAMPAIGN_PYTHON']
    plan.update(timing(datetime.datetime.now(datetime.timezone.utc)))
    module.write(root / 'series.json', plan)
    unit = 'mqttbenchmark-compact-' + uuid.uuid4().hex + '.service'
    runner = plan['orchestrationAdapter']
    args = [python, runner, '--series', str(root), '--resume']
    cleanup = [python, runner, 'cleanup', '--series', str(root)]
    cpus = set()
    for part in plan['controllerCpuSet'].split(','):
        ends = part.split('-')
        cpus.update(range(int(ends[0]), int(ends[-1]) + 1))
    mask = bytearray(max(cpus) // 8 + 1)
    for cpu in cpus:
        mask[cpu // 8] |= 1 << (cpu % 8)

    def execution(argv):
        return dbus.Array([dbus.Struct((dbus.String(argv[0]), dbus.Array(argv, signature='s'), dbus.Boolean(False)))], signature='(sasb)')

    properties = [
        ('Description', dbus.String('MqttBenchmark compact-v1 six-hour integrity campaign')),
        ('Type', dbus.String('exec')), ('CollectMode', dbus.String('inactive-or-failed')),
        ('WorkingDirectory', dbus.String(str(root))),
        ('StandardOutputFileToAppend', dbus.String(str(root / 'logs/controller.stdout.log'))),
        ('StandardErrorFileToAppend', dbus.String(str(root / 'logs/controller.stderr.log'))),
        ('Environment', dbus.Array([key + '=' + value for key, value in os.environ.items()], signature='s')),
        ('ExecStart', execution(args)), ('ExecStopPost', execution(cleanup)),
        ('MemoryMax', dbus.UInt64(plan['controllerMemoryMaxBytes'])),
        ('CPUAffinity', dbus.ByteArray(mask)), ('KillMode', dbus.String('control-group')),
        ('KillSignal', dbus.Int32(2)), ('TimeoutStopUSec', dbus.UInt64(plan['stopTimeoutSeconds'] * 1000000)),
        ('RuntimeMaxUSec', dbus.UInt64(plan['runtimeMaxSeconds'] * 1000000))]
    bus = dbus.bus.BusConnection('unix:path=/run/user/%d/bus' % os.getuid())
    manager = dbus.Interface(bus.get_object('org.freedesktop.systemd1', '/org/freedesktop/systemd1'), 'org.freedesktop.systemd1.Manager')
    module.write(root / 'controller-service.json', {'unit': unit, 'argv': args, 'cleanupArgv': cleanup,
        'cpuSet': plan['controllerCpuSet'], 'memoryMaxBytes': plan['controllerMemoryMaxBytes'],
        'launcherSha256': module.digest(__file__), 'timing': timing(datetime.datetime.fromisoformat(plan['startedAtUtc']))})
    manager.StartTransientUnit(unit, 'fail', properties, [], signature='ssa(sv)a(sa(sv))')
    print(json.dumps({'unit': unit, 'deadlineUtc': plan['deadlineUtc']}))


if __name__ == '__main__':
    main()
