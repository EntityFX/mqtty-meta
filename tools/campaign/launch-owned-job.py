"""Launch a bounded owned job over user D-Bus; never replaces a running campaign unit."""
import datetime
import json
import os
import pathlib
import sys
import dbus

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
deadline = datetime.datetime.fromisoformat(payload['deadlineUtc'])
remaining = int((deadline - datetime.datetime.now(datetime.timezone.utc)).total_seconds()) - 60
if remaining <= 0:
    raise RuntimeError('Original deadline exhausted; job will not start')
argv = payload['argv']
def command(args):
    return dbus.Array([dbus.Struct((dbus.String(args[0]), dbus.Array(args, signature='s'), dbus.Boolean(False)))], signature='(sasb)')
properties = [('Description', dbus.String(payload['description'])), ('Type', dbus.String('exec')),
    ('WorkingDirectory', dbus.String(payload['workingDirectory'])), ('MemoryMax', dbus.UInt64(payload['memoryMaxBytes'])),
    ('RuntimeMaxUSec', dbus.UInt64(remaining * 1000000)), ('TimeoutStopUSec', dbus.UInt64(30 * 1000000)),
    ('KillSignal', dbus.Int32(2)), ('KillMode', dbus.String('control-group')),
    ('Environment', dbus.Array([key + '=' + value for key, value in os.environ.items()], signature='s')),
    ('ExecStart', command(argv)), ('StandardOutputFileToAppend', dbus.String(payload['stdout'])),
    ('StandardErrorFileToAppend', dbus.String(payload['stderr']))]
mask = bytearray(max(payload['cpus']) // 8 + 1)
for cpu in payload['cpus']:
    mask[cpu // 8] |= 1 << (cpu % 8)
properties.append(('CPUAffinity', dbus.ByteArray(mask)))
if payload.get('cleanupArgv'):
    properties.append(('ExecStopPost', command(payload['cleanupArgv'])))
bus = dbus.bus.BusConnection('unix:path=/run/user/%d/bus' % os.getuid())
manager = dbus.Interface(bus.get_object('org.freedesktop.systemd1', '/org/freedesktop/systemd1'), 'org.freedesktop.systemd1.Manager')
manager.StartTransientUnit(payload['unit'], 'fail', properties, [], signature='ssa(sv)a(sa(sv))')
print(payload['unit'])
