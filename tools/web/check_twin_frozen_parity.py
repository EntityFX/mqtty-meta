"""Compare a completed Web MODEL job with the preserved article-1 probe on Linux."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block:=stream.read(1024*1024):value.update(block)
    return value.hexdigest()


def run(probe,web_output,output):
    probe=Path(probe).resolve(strict=True)
    web_output=Path(web_output).resolve(strict=True)
    output=Path(output).resolve()
    mount=Path('/run/media/entityfx/hd250')
    if not mount.is_mount() or mount not in output.parents or output==mount:
        raise ValueError('Require a new output directory on the mounted evidence disk')
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True)
    identity={p.name:digest(p) for p in probe.parent.iterdir() if p.is_file()}
    config=json.loads((web_output/'twin-validation.json').read_text())['configuration']
    assert digest(config['profilePath'])==config['profileSha256']
    config.update(id='web-frozen-parity',campaignId='web-twin-validation-v1',repeat=1)
    config_path=output/'config.json'
    config_path.write_text(json.dumps(config,indent=2)+'\n')
    receipt={'passed':False,'scope':'Model execution parity; not a new native accuracy result',
             'probe':str(probe),'webOutput':str(web_output),'profileSha256':config['profileSha256']}
    started=time.monotonic()
    try:
        with (output/'stdout.log').open('wb') as stdout,(output/'stderr.log').open('wb') as stderr:
            process=subprocess.run(['dotnet',str(probe),'model',str(config_path),str(output/'direct')],
                                   cwd=probe.parent,stdout=stdout,stderr=stderr,timeout=60)
        receipt['exitCode']=process.returncode
        if process.returncode:raise RuntimeError('Frozen probe exited with '+str(process.returncode))
        direct_summary=next((output/'direct').glob('*/summary.json'))
        names=['summary.json','latency-histogram.json','publishes.ndjson','deliveries.ndjson','connections.ndjson','subscriptions.ndjson','phases.json']
        matches={name:digest(web_output/name)==digest(direct_summary.parent/name) for name in names}
        receipt.update(matches=matches,directRunDirectory=str(direct_summary.parent),
                       summarySha256=digest(direct_summary))
        if not all(matches.values()):raise AssertionError('Direct/Web scientific files differ')
        receipt['passed']=True
    except Exception as error:
        receipt['error']=type(error).__name__+': '+str(error)
    finally:
        receipt['frozenRuntimeUnchanged']=identity=={p.name:digest(p) for p in probe.parent.iterdir() if p.is_file()}
        receipt['runtimeFilesVerified']=len(identity)
        if not receipt['frozenRuntimeUnchanged']:receipt['passed']=False
        receipt['elapsedSeconds']=time.monotonic()-started
        (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))
    if not receipt['passed']:raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe',required=True)
    parser.add_argument('--web-output',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    run(args.probe,args.web_output,args.output)
