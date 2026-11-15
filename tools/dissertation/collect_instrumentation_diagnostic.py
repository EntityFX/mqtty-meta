"""Export diagnostic raw, preserving exact hashes while excluding unchanged broker copies."""
import hashlib
import json
from pathlib import Path
import tarfile

root = Path('/run/media/entityfx/hd250/mqtty-evidence/article1-instrumentation-diagnostic-v1')
archive = root.parent/'article1-instrumentation-diagnostic-v1-raw-001.tar.gz'
index_path = archive.with_suffix('').with_suffix('.json')
if archive.exists() or index_path.exists():
    raise FileExistsError('Never replace an earlier raw export')
files = {}
with tarfile.open(archive, 'x:gz') as stream:
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if 'broker-home' in relative.parts and relative.parts[-2:] != ('conf', 'activemq.xml'):
            continue
        name = str(relative)
        stream.add(path, arcname=name, recursive=False)
        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
receipt = dict(archive=str(archive), archiveSha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
               archiveBytes=archive.stat().st_size, files=files,
               exclusion='Unchanged broker-home copies excluded except effective conf/activemq.xml; source pins in program.json')
index_path.write_text(json.dumps(receipt, indent=2)+'\n')
print(json.dumps({k:v for k,v in receipt.items() if k!='files'}))
