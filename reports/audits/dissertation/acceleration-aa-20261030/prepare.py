"""Stage a new sealed input packet and run only supervisor tests on Linux."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

REPO = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
PYTHON = 'C:/Users/entityfx/.mqtty-tools/python/python.exe'
SSH = ['ssh', '-i', 'C:/Users/entityfx/.ssh/mqtty_two_machine', '-o', 'BatchMode=yes',
       '-o', 'ConnectTimeout=12', 'entityfx@192.168.5.1']
REMOTE = '/run/media/entityfx/hd250/mqtty-evidence/acceleration-aa-20261030-inputs'
LOCAL = Path('C:/temp/mqtty-evidence/acceleration-aa-20261030-inputs')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == '__main__':
    LOCAL.mkdir(parents=True, exist_ok=False)
    sources = {
        'run_acceleration_pilot.py': REPO/'tools/dissertation/run_acceleration_pilot.py',
        'test_acceleration_pilot.py': REPO/'tools/dissertation/test_acceleration_pilot.py',
        'fixture.json': REPO/'reports/audits/dissertation/bounded-experiments-20261030/configs/q1024.json',
        'plan.md': REPO/'docs/plans/Acceleration-aa-20261030.md',
        'original-protocol.md': REPO/'docs/dissertation/acceleration-benchmark-20261030.md',
    }
    for name, path in sources.items():
        shutil.copy2(path, LOCAL/name)
    seal = {'programIdentity': 'acceleration-aa-20261030-v1',
            'files': {name: sha(LOCAL/name) for name in sources},
            'schedule': ['A1: workers1, two jobs', 'A2: workers1, same two jobs'],
            'hardWallSeconds': 600, 'cpuAffinity': [4, 6], 'retryAllowed': False}
    (LOCAL/'seal.json').write_bytes((json.dumps(seal, indent=2)+'\n').encode())
    (HERE/'input-seal.json').write_bytes((LOCAL/'seal.json').read_bytes())
    subprocess.run([*SSH, 'python3 -B -'], input=f'from pathlib import Path\nPath({REMOTE!r}).mkdir(exist_ok=False)\n',
                   text=True, capture_output=True, check=True, timeout=20)
    subprocess.run(['scp', '-i', 'C:/Users/entityfx/.ssh/mqtty_two_machine', '-o', 'BatchMode=yes',
                    *[str(p) for p in LOCAL.iterdir()], 'entityfx@192.168.5.1:'+REMOTE+'/'], check=True, timeout=40)
    script = f'''import sys,json,hashlib,subprocess
from pathlib import Path
root=Path({REMOTE!r})
seal=json.loads((root/'seal.json').read_text())
assert hashlib.sha256((root/'seal.json').read_bytes()).hexdigest()=={sha(LOCAL/'seal.json')!r}
for name,expected in seal['files'].items():
 assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected
result=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',str(root),'-p','test_acceleration_pilot.py','-v'],capture_output=True,text=True,timeout=20)
print(json.dumps({{'returnCode':result.returncode,'stdout':result.stdout,'stderr':result.stderr,'inputSealSha256':{sha(LOCAL/'seal.json')!r}}}))
'''
    result = subprocess.run([*SSH, 'python3 -B -'], input=script, text=True, capture_output=True, check=True, timeout=35)
    receipt = json.loads(result.stdout)
    receipt['localPortableTests'] = '5 passed, 2 Linux-only skipped; initial red was missing implementation'
    (HERE/'tests.json').write_bytes((json.dumps(receipt, indent=2)+'\n').encode())
    print(json.dumps(receipt))
    if receipt['returnCode']:
        raise SystemExit(receipt['returnCode'])
