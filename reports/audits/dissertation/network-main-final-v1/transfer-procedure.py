from pathlib import Path
import tarfile,json,hashlib,sys,time
base=Path('C:/temp/mqtty-evidence/network-final-20261028'); receipt=json.loads((base/'archive.json').read_text()); archive=base/'network-full-final.tar.gz'
assert archive.stat().st_size==receipt['bytes']; assert hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()==receipt['sha256']
root=base/'raw-002';root.mkdir(exist_ok=False)
with tarfile.open(archive) as t:t.extractall(root,filter='data')
index=json.loads((base/'file-index.json').read_text()); actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}; assert actual=={r['path'] for r in index['files']}
for i,r in enumerate(index['files'],1):
 p=root/r['path'];assert p.stat().st_size==r['bytes'];assert hashlib.file_digest(p.open('rb'),'sha256').hexdigest()==r['sha256'],r['path']
 if i%1000==0:print('Verified',i,'files',flush=True)
out={'status':'allFilesVerified','archive':receipt,'rawRoot':str(root),'verifiedFiles':len(actual),'totalRawBytes':index['totalBytes'],'fileIndexSha256':hashlib.file_digest((base/'file-index.json').open('rb'),'sha256').hexdigest(),'originalRaw001Preserved':True}
(base/'transfer-verification.json').write_bytes((json.dumps(out,indent=2)+'\n').encode());print(json.dumps(out),flush=True)
