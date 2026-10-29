"""Generate the strictly minimal graph execution adapter; never edit original code."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parents[2]
SOURCE = 'src/EntityFX.MqttSimulator/EntityFX.MqttY.Federation.App/Execution/FederationRunner.cs'
COMMIT = 'f5c0a4715b555c4348d2a2843177251f62c640ff'


def adapt(source):
    replacements = [('public sealed class FederationRunner', 'public sealed class GraphExecutionRunner'),
                    ('Run(FederationInput input, string? output = null', 'Run(FederationInput input, bool parallel, string? output = null'),
                    ('Refresh(false, 0)', 'Refresh(parallel, 0)')]
    for old, new in replacements:
        if source.count(old) != 1:
            raise ValueError('Source does not match audited adapter: '+old)
        source = source.replace(old, new)
    return source


def prepare(output):
    if subprocess.check_output(['git','-C',str(REPO/'mqtty'),'rev-parse','HEAD'],text=True).strip() != COMMIT:
        raise ValueError('Unexpected MqttY source')
    original = subprocess.check_output(['git','-C',str(REPO/'mqtty'),'show',COMMIT+':'+SOURCE])
    current = REPO/'mqtty'/SOURCE
    if current.read_text(encoding='utf-8').replace('\r\n','\n') != original.decode().replace('\r\n','\n'):
        raise ValueError('Original runner has uncommitted changes')
    output.mkdir(parents=True,exist_ok=False)
    (output/'FederationRunner.original.cs').write_bytes(original)
    generated = adapt(original.decode()).encode()
    (output/'Runner.generated.cs').write_bytes(generated)
    manifest={'mqttySourceCommit':COMMIT,'sourcePath':SOURCE,
              'originalSha256':hashlib.sha256(original).hexdigest(),
              'generatedSha256':hashlib.sha256(generated).hexdigest(),
              'changes':['class name','bool parallel argument','Refresh bool only'],
              'oldRuntimeChanged':False}
    (output/'adapter.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path)
    prepare(parser.parse_args().output)
