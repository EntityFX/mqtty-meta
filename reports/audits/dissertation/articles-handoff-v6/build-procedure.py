"""Assemble current papers and accepted examples; do not run new experiments.

The archive remains outside Git. Previous versions and scientific inputs are read only.
"""
from pathlib import Path, PurePosixPath
import hashlib
import json
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[4]
AUDIT = Path(__file__).resolve().parent
OUT = Path('C:/temp/mqtty-evidence/article-portable-v6')
checks = []


def check(value, label):
    if not value:
        raise AssertionError(label)
    checks.append(label)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def links(text):
    text = re.sub(r'(?ms)^```[^\n]*\n.*?^```[^\n]*$', '', text)
    for target in re.findall(r'\]\(([^)]+)\)', text):
        target = target.strip('<>').split('#')[0]
        if target and not re.match(r'[a-zA-Z][a-zA-Z0-9+.-]*:', target):
            yield target


def protected_hashes():
    result = {}
    names = subprocess.check_output(['git', '-C', str(ROOT/'diss'), 'ls-files', '-z'])
    for name in names.decode('utf-8').split('\0'):
        path = ROOT/'diss'/name
        if name and path.is_file():
            result['diss/'+name] = sha(path)
    for path in (ROOT/'docs/dissertation/articles').glob('*'):
        if path.is_file():
            result[path.relative_to(ROOT).as_posix()] = sha(path)
    return result


def main():
    protected = protected_hashes()
    source_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    old = load(ROOT/'reports/audits/dissertation/articles-handoff-v5/verification.json')
    check(sha(old['zip']) == old['zipSha256'], 'previous ZIP v5 SHA verified')
    check(not OUT.exists(), 'new output; previous packages are not overwritten')
    OUT.mkdir()
    bundle = OUT/'bundle'
    bundle.mkdir()
    # Retain only the three sealed examples, their verifiers and dependencies.
    # Current prose/evidence is copied from the repository, never from the old ZIP.
    with zipfile.ZipFile(old['zip']) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        for name in archive.namelist():
            path = PurePosixPath(name)
            check(not path.is_absolute() and '..' not in path.parts and ':' not in name,
                  'safe previous member '+name)
            if name == 'manifest.json':
                continue
            data = archive.read(name)
            check(hashlib.sha256(data).hexdigest() == manifest['files'][name],
                  'previous sealed member '+name)
            if name.startswith(('example/', 'twins/', 'floor/', 'tools/')) or name in (
                    'verify_example.py', 'verify_twins.py', 'verify_floor.py'):
                target = bundle/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)

    copied = {}

    def copy(path):
        path = Path(path).resolve()
        check(path.is_relative_to(ROOT) and path.is_file(), 'source file '+str(path.relative_to(ROOT)))
        relative = path.relative_to(ROOT).as_posix()
        target = bundle/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        copied[relative] = sha(path)

    articles = [ROOT/'docs/dissertation/articles'/name for name in (
        'article-1-twin-validation.md', 'article-2-relay-federation.md')]
    for article in articles:
        copy(article)
        for link in links(article.read_text(encoding='utf-8-sig')):
            copy(article.parent/link)

    current = ROOT/'reports/audits/dissertation/articles-final-review-20261101'
    # Run current paper checks before sealing and keep their exact inputs.
    completed = subprocess.run([sys.executable, '-I', '-B', str(current/'verify.py')],
                               cwd=OUT, capture_output=True, text=True, timeout=120)
    check(completed.returncode == 0, 'current 425 paper checks: '+completed.stderr)
    review = load(current/'verification.json')
    check(review['checksPassed'] == 425 and review['localLinksChecked'] == 41, '425 checks / 41 article links')
    for relative in review['inputsSha256']:
        copy(ROOT/relative)
    for relative in (
        'docs/dissertation/articles/citation-evidence.md',
        'docs/dissertation/reproduction-handoff.md',
        'reports/analysis/Articles-final-review-20261101.md',
        'reports/analysis/Dissertation-evidence-integration.md',
        'reports/analysis/Linux-selected-20261030.md',
        'reports/analysis/Queue-main-window-20261030.md',
        'reports/audits/dissertation/articles-final-review-20261101/verify.py',
        'reports/audits/dissertation/articles-final-review-20261101/verification.json',
        'reports/audits/dissertation/article-1-matched-main-final-v1/native-model.csv',
        'reports/audits/dissertation/article-1-matched-main-final-v1/phase-ticks.csv',
        'reports/audits/dissertation/linux-selected-20261030/temporal-comparison.csv',
        'reports/audits/dissertation/linux-selected-20261030/remaining-result/acceptance.json',
        'reports/audits/dissertation/network-main-final-v1/acceptance.json',
        'reports/audits/dissertation/network-main-final-v1/raw-recount/comparison.csv',
        'reports/audits/dissertation/network-main-final-v1/transit.csv',
        'reports/audits/dissertation/parallel-block-20261030/topology-correction.md',
    ):
        copy(ROOT/relative)
    copy(AUDIT/'Reader-guide.md')
    copy(__file__)
    (bundle/'README.txt').write_bytes((AUDIT/'Reader-guide.md').read_bytes())
    (bundle/'verify_all.py').write_text(
        "from pathlib import Path\nimport runpy,sys\nsys.dont_write_bytecode=True\n"
        "base=Path(__file__).resolve().parent\n"
        "for name in ('verify_example.py','verify_twins.py','verify_floor.py',"
        "'reports/audits/dissertation/articles-final-review-20261101/verify.py'):\n"
        "    runpy.run_path(str(base/name),run_name='__main__')\n",
        encoding='utf-8')

    external = []
    for document in sorted(bundle.rglob('*.md')):
        for target in links(document.read_text(encoding='utf-8-sig')):
            resolved = (document.parent/target).resolve()
            if resolved.is_file():
                continue
            relative = document.relative_to(bundle)
            original = (ROOT/relative).parent/target
            original = original.resolve()
            external.append({'source':relative.as_posix(), 'target':target,
                             'repositoryPath': original.relative_to(ROOT).as_posix() if original.is_relative_to(ROOT) else None})
    (bundle/'external-references.json').write_text(json.dumps(
        {'sourceCommit':source_commit, 'repository':'https://github.com/EntityFX/mqtty-meta',
         'scope':'Further reading in supporting reports, outside the compact package',
         'links':external}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    sealed = {p.relative_to(bundle).as_posix():sha(p) for p in sorted(bundle.rglob('*')) if p.is_file()}
    (bundle/'manifest.json').write_text(json.dumps(
        {'version':6,'date':'2026-11-02','sourceCommit':source_commit,
         'previousZipSha256':old['zipSha256'],'scope':'Current papers, accepted compact evidence and three raw examples',
         'files':sealed}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    archive_path = OUT/'reproduction-examples.zip'
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(bundle).as_posix(), (2026,11,2,0,0,0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
    fresh = OUT/'fresh'
    fresh.mkdir()
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(fresh)
    result = subprocess.run([sys.executable,'-I','-B',str(fresh/'verify_all.py')],
                            cwd=OUT, capture_output=True, text=True, timeout=180)
    (AUDIT/'fresh-verification.stdout.log').write_text(result.stdout+result.stderr, encoding='utf-8')
    check(result.returncode == 0, 'fresh extraction independent of checkout: '+result.stderr)
    check('"checksPassed": 425' in result.stdout, 'fresh copy recalculates current paper tables')
    check(all(sha(fresh/name) == digest for name,digest in sealed.items()), 'files unchanged by verification')
    for article in articles:
        check((fresh/article.relative_to(ROOT)).read_bytes() == article.read_bytes(), 'current paper exact bytes')
    readme = fresh/'README.txt'
    original = readme.read_bytes()
    readme.write_bytes(original+b'\nCORRUPTED\n')
    bad = subprocess.run([sys.executable,'-I','-B',str(fresh/'verify_all.py')],
                         cwd=OUT, capture_output=True, text=True, timeout=60)
    readme.write_bytes(original)
    check(bad.returncode != 0 and 'README.txt' in bad.stderr, 'changed file rejected by seal')
    manifest_path = fresh/'manifest.json'
    original_manifest = manifest_path.read_bytes()
    altered = json.loads(original_manifest)
    altered['files']['README.txt'] = '0'*64
    manifest_path.write_text(json.dumps(altered), encoding='utf-8')
    bad_manifest = subprocess.run([sys.executable,'-I','-B',str(fresh/'verify_all.py')],
                                  cwd=OUT, capture_output=True, text=True, timeout=60)
    manifest_path.write_bytes(original_manifest)
    check(bad_manifest.returncode != 0 and 'README.txt' in bad_manifest.stderr, 'changed manifest digest rejected')
    check(protected_hashes() == protected, 'dissertation and article sources unchanged')
    check(all(sha(ROOT/name) == digest for name,digest in copied.items()), 'copied repository inputs unchanged')
    check(sha(old['zip']) == old['zipSha256'], 'previous ZIP remains unchanged')
    receipt = {'status':'verified','version':6,'dateMsk':'2026-11-02','sourceCommit':source_commit,
               'zip':str(archive_path),'zipBytes':archive_path.stat().st_size,'zipSha256':sha(archive_path),
               'previousZipSha256':old['zipSha256'],'filesSealed':len(sealed),
               'freshExtractionPassed':True,'changedFileRejected':True,'changedManifestDigestRejected':True,
               'currentManuscriptBytesMatch':True,'articleChecksPassed':425,'articleLocalLinksChecked':41,
               'rawExamplesChecked':3,'fullSeriesRawRecountPerformed':False,
               'checksPassed':len(checks),'repositoryInputsSha256':copied,
               'protectedSourceFilesChecked':len(protected),'protectedSourcesUnchanged':True,
               'procedureSha256':sha(__file__),'extraReportLinksOutsidePackage':len(external),
               'newExperiments':0,'linuxPolled':False,'humanReviewPassed':False,'publiclyHosted':False,
               'globalPercent':53.2,'weightedStepsAdded':[]}
    (AUDIT/'verification.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:receipt[k] for k in ('status','zipBytes','filesSealed','checksPassed','articleChecksPassed','protectedSourcesUnchanged')}))


if __name__ == '__main__':
    main()
