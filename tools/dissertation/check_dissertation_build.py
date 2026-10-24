"""Audit and optionally build only the main dissertation in a fresh isolated snapshot.

No source rewrite, package installation or automatic download is performed. A successful
exit is a technical compilation check, never acceptance of scientific content/layout.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time


ROOT = Path(__file__).resolve().parents[2]
GENERATED = {'.aux', '.log', '.lof', '.lot', '.nlo', '.nls', '.toc', '.bcf',
             '.bbl', '.blg', '.fls', '.out', '.xdv', '.synctex.gz'}
TOOLS = ('latexmk', 'xelatex', 'biber', 'kpsewhich', 'makeindex', 'pdfinfo', 'pdftotext')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def uncomment(text):
    return re.sub(r'(?<!\\)%[^\n]*', '', text)


def tracked_inputs(source):
    result = subprocess.run(['git', '-C', str(source), 'ls-files', '-z'],
                            capture_output=True, check=True)
    paths = []
    for name in result.stdout.decode('utf-8').split('\0'):
        if not name:
            continue
        path = source / name
        if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
            raise ValueError(f'Unsafe source input: {name}')
        if path.suffix in GENERATED or name in {'dissertation.pdf', 'dissertation.md'}:
            continue
        paths.append(path)
    return sorted(paths)


def source_audit(source):
    main = uncomment((source / 'dissertation.tex').read_text(encoding='utf-8'))
    includes = re.findall(r'\\include\s*\{([^}]+)\}', main)
    inputs = re.findall(r'\\input\s*\{([^}]+)\}', main)
    files = [p for p in tracked_inputs(source) if p.suffix == '.tex']
    packages = set()
    for path in files:
        text = uncomment(path.read_text(encoding='utf-8'))
        for match in re.finditer(r'\\(?:usepackage|RequirePackage)(?:\[[^\]]*\])?\s*\{([^}]+)\}', text):
            packages.update(p.strip() for p in match.group(1).split(','))
    missing = [name for name in inputs + includes if not (source / (name + '.tex')).is_file()]
    return {'entrypoint': 'dissertation.tex', 'topLevelIncludes': includes,
            'topLevelInputs': inputs, 'missingTopLevelFiles': missing,
            'includeOnlyActive': bool(re.search(r'\\includeonly\s*\{', main)),
            'allTrackedTexPackagesConservativeSuperset': sorted(packages),
            'bibliographyBackend': 'biber (common/setup.tex default bibliosel=1)',
            'bibliographies': ['biblio/external.bib', 'biblio/author.bib', 'biblio/registered.bib'],
            'fontPolicy': 'source default: Microsoft fonts; no override',
            'legacyReadmeEntrypoint': 'latex/Dissertation.tex (different document, not used)'}


def log_issues(text):
    patterns = {'undefinedCitation': r'Citation .+ undefined|undefined citations',
                'undefinedReference': r'Reference .+ undefined|undefined references',
                'missingGlyph': r'Missing character:',
                'latexError': r'(?:^! |LaTeX Error:|Package .+ Error:)',
                'rerunRequested': r'Please \(re\)run Biber|Rerun to get cross-references right'}
    return {key: re.findall(pattern, text, flags=re.M) for key, pattern in patterns.items()}


def bounded_build(command, snapshot, env, stdout, timeout):
    """Keep a timeout from leaving TeX/Biber children behind."""
    process = subprocess.Popen(command, cwd=snapshot, env=env, stdout=stdout,
                               stderr=subprocess.STDOUT, start_new_session=os.name != 'nt')
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)
        raise


def validate_fresh_pdf(snapshot, includes, commands):
    pdf = snapshot / 'dissertation.pdf'
    if not pdf.is_file() or pdf.stat().st_size == 0:
        raise ValueError('Fresh main PDF missing')
    log = (snapshot / 'dissertation.log').read_text(encoding='utf-8', errors='replace')
    issues = log_issues(log)
    recorder = (snapshot / 'dissertation.fls').read_text(encoding='utf-8', errors='replace')
    loaded = {line[6:].replace('\\', '/').removeprefix('./') for line in recorder.splitlines()
              if line.startswith('INPUT ')}
    absent = [name + '.tex' for name in includes if not any(
        entry == name + '.tex' or entry.endswith('/' + name + '.tex') for entry in loaded)]
    bbl = snapshot / 'dissertation.bbl'
    if not bbl.is_file() or not re.search(r'\\entry\{', bbl.read_text(encoding='utf-8', errors='replace')):
        issues['bibliographyMissing'] = ['No nonempty biblatex bibliography']
    info = subprocess.run([commands['pdfinfo'], str(pdf)], capture_output=True, check=True)
    text = subprocess.run([commands['pdftotext'], str(pdf), '-'], capture_output=True, check=True)
    metadata = info.stdout.decode('utf-8', errors='replace')
    pages = re.search(r'^Pages:\s+(\d+)', metadata, re.M)
    if not pages or int(pages.group(1)) < 1 or not text.stdout.strip():
        raise ValueError('PDF metadata/text extraction failed')
    return {'pdfSha256': sha(pdf), 'pages': int(pages.group(1)), 'pdfinfo': metadata,
            'extractedTextSha256': hashlib.sha256(text.stdout).hexdigest(),
            'unloadedTopLevelIncludes': absent, 'logIssues': issues,
            'technicalChecksPassed': not absent and not any(issues.values()),
            'visualReview': 'not performed; required separately'}


def run(source, output, build=False, timeout=300):
    source, output = source.resolve(), output.resolve()
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('Output must be a new directory outside the source tree')
    output.mkdir(parents=True)
    paths = tracked_inputs(source)
    before = {str(p.relative_to(source)).replace('\\', '/'): sha(p) for p in paths}
    audit = source_audit(source)
    commands = {tool: shutil.which(tool) for tool in TOOLS}
    record = {'schema': 'main-dissertation-build-v1', 'sourceRoot': str(source),
              'sourceCommit': subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD']).decode().strip(),
              'inputs': before, 'audit': audit, 'commands': commands, 'buildRequested': build,
              'status': 'auditOnly', 'step37Accepted': False,
              'buildCommand': ['latexmk', '-norc', '-r', 'latexmkrc', '-pdfxe',
                               '-interaction=nonstopmode', '-halt-on-error', '-file-line-error', 'dissertation.tex']}
    missing = [name for name, value in commands.items() if not value]
    record['missingTools'] = missing
    record['toolVersions'] = {}
    for name, command in commands.items():
        if command:
            result = subprocess.run([command, '-v' if name in {'pdfinfo', 'pdftotext'} else '--version'],
                                    capture_output=True, timeout=15)
            record['toolVersions'][name] = (result.stdout + result.stderr).decode('utf-8', errors='replace')
    try:
        if build and missing:
            record['status'] = 'blockedMissingTools'
        elif build:
            if audit['missingTopLevelFiles'] or audit['includeOnlyActive']:
                raise ValueError('Main document has missing includes or active includeonly')
            snapshot = output / 'snapshot'
            for path in paths:
                target = snapshot / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            env = os.environ.copy()
            for key in ('DRAFTON', 'SHOWMARKUP', 'FONTFAMILY', 'ALTFONT', 'USEBIBER',
                        'USEFOOTCITE', 'BIBGROUPED', 'IMGCOMPILE', 'NOTESON', 'USEDEV',
                        'LATEXFLAGS', 'BIBERFLAGS', 'TIKZFILE'):
                env.pop(key, None)
            record['engineEnvironment'] = 'source defaults; build-changing inherited overrides removed'
            started = time.monotonic()
            with (output / 'build.stdout.log').open('wb') as stdout:
                exit_code = bounded_build(record['buildCommand'], snapshot, env, stdout, timeout)
            record['elapsedSeconds'] = time.monotonic() - started
            record['exitCode'] = exit_code
            record['status'] = 'compilationFailed' if exit_code else 'compiled'
            if not exit_code:
                record['validation'] = validate_fresh_pdf(snapshot, audit['topLevelIncludes'], commands)
                record['status'] = 'technicalChecksPassed' if record['validation']['technicalChecksPassed'] else 'pdfChecksFailed'
    except Exception as error:
        record.update(status='technicalError', errorType=type(error).__name__, error=str(error))
    finally:
        record['sourceUnchanged'] = all(p.is_file() and sha(p) == before[str(p.relative_to(source)).replace('\\', '/')] for p in paths)
        if not record['sourceUnchanged']:
            record['status'] = 'sourceChangedDuringAudit'
        (output / 'verification.json').write_bytes((json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'diss')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 300:
        parser.error('timeout must be between 1 and 300 seconds')
    record = run(args.source, args.output, args.build, args.timeout)
    print(json.dumps({key: record[key] for key in ('status', 'missingTools', 'sourceUnchanged', 'step37Accepted')}, ensure_ascii=False))
    return 0 if record['status'] in {'auditOnly', 'technicalChecksPassed'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
