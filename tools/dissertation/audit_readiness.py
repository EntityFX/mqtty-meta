"""Read-only source audit; does not compile TeX or establish scientific validity."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def uncomment(text):
    lines = []
    for line in text.splitlines(keepends=True):
        for index, char in enumerate(line):
            if char == "%":
                backslashes = 0
                cursor = index - 1
                while cursor >= 0 and line[cursor] == "\\":
                    backslashes += 1
                    cursor -= 1
                if backslashes % 2 == 0:
                    line = line[:index] + ("\n" if line.endswith("\n") else "")
                    break
        lines.append(line)
    return "".join(lines)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def command(*args, cwd=ROOT):
    return subprocess.check_output(args, cwd=cwd, text=True, encoding="utf-8").strip()


def inspect_tex(base):
    base = Path(base).resolve()
    reachable, missing_inputs, hashes = {}, [], {}
    pending = [base / "dissertation.tex"]
    while pending:
        path = pending.pop()
        name = path.relative_to(base).as_posix()
        if name in reachable:
            continue
        text = uncomment(path.read_text(encoding="utf-8-sig"))
        reachable[name] = text
        hashes[name] = sha(path)
        for match in re.finditer(r"\\(?:input|include)\s*\{([^{}]+)\}", text):
            relative = match[1]
            if "\\" in relative or "#" in relative:
                continue
            target = base / (relative if Path(relative).suffix else relative + ".tex")
            if target.is_file():
                pending.append(target)
            else:
                missing_inputs.append(dict(file=name, line=text[:match.start()].count("\n") + 1, target=relative))
    labels, references, citations = defaultdict(list), defaultdict(list), defaultdict(list)
    resources = sorted((base / "biblio").glob("*.bib"))
    keys = defaultdict(list)
    for resource in resources:
        for match in re.finditer(r"(?mi)^\s*@(?!(?:comment|string|preamble)\b)(\w+)\s*\{\s*([^,\s]+)", resource.read_text(encoding="utf-8-sig")):
            keys[match[2]].append(resource.relative_to(base).as_posix())
        hashes[resource.relative_to(base).as_posix()] = sha(resource)
    images, empty_items = [], []
    for name, text in sorted(reachable.items()):
        for match in re.finditer(r"\\(label|[Cc]ref|[Cc]pageref|[Vv]ref|eqref|pageref|autoref|ref)\*?\s*\{([^{}]*)\}", text):
            target = labels if match[1] == "label" else references
            for key in match[2].split(","):
                key = key.strip()
                if "\\" not in key and "#" not in key:
                    target[key].append(dict(file=name, line=text[:match.start()].count("\n") + 1))
        for match in re.finditer(r"\\(?:[Cc]ite|[Cc]itep|[Cc]itet|[Cc]ites|[Aa]utocite|[Pp]arencite|[Tt]extcite|[Ff]ootcite|smartcite|supercite|nocite)\*?(?:\s*\[[^\]]*\])*\s*\{([^{}]*)\}", text):
            for key in match[1].split(","):
                key = key.strip()
                if key != "*" and "#" not in key and "\\" not in key:
                    citations[key].append(dict(file=name, line=text[:match.start()].count("\n") + 1))
        for match in re.finditer(r"\\includegraphics\*?(?:\s*\[[^\]]*\])?\s*\{([^{}]+)\}", text):
            relative = match[1]
            if "\\" in relative or "#" in relative:
                continue
            candidates = [folder / relative for folder in (base, base / Path(name).parent, base / "images", base / "Dissertation/images")]
            candidates = [p.with_suffix(ext) if not p.suffix else p for p in candidates for ext in (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg")]
            images.append(dict(file=name, line=text[:match.start()].count("\n") + 1, target=relative,
                               conditionalGuardNearby="\\IfFileExists" in text[max(0, match.start()-250):match.start()],
                               resolved=any(p.is_file() for p in candidates)))
        for match in re.finditer(r"\\item(?:\s*\[[^\]]*\])?\s*(?=\\(?:item|end\{))", text):
            empty_items.append(dict(file=name, line=text[:match.start()].count("\n") + 1))
    signatures = ["1512", "Fidelity", "holdout", "broker-calibration", "LoadBalancing.App", "Federation.App", "network-evidence.json"]
    combined = "\n".join(reachable.values())
    return dict(root="dissertation.tex", staticOnly=True, conditionalBranchesNotEvaluated=True,
        reachableFiles=sorted(reachable), sourceHashes=hashes, missingInputs=missing_inputs,
        duplicateLabels={key: locations for key, locations in labels.items() if len(locations) > 1},
        unresolvedReferences={key: locations for key, locations in references.items() if key not in labels},
        unresolvedCitationKeys={key: locations for key, locations in citations.items() if key not in keys},
        duplicateBibliographyKeys={key: files for key, files in keys.items() if len(files) > 1},
        citedKeys=len(citations), bibliographyKeys=len(keys), images=images, emptyItems=empty_items,
        modernCampaignSignatures={s: s.casefold() in combined.casefold() for s in signatures})


def inspect_publications(root):
    result = {}
    for folder in [root / "reports/data", *(root / "reports/data").iterdir()]:
        registry = folder / "publication-sha256.json"
        if not registry.is_file():
            continue
        errors, checked = [], 0
        for relative, digest in json.loads(registry.read_text(encoding="utf-8-sig")).items():
            path = (root / "reports" / relative).resolve()
            if not path.is_relative_to((root / "reports").resolve()) or not path.is_file() or sha(path) != digest:
                errors.append(relative)
            checked += 1
        result[folder.relative_to(root).as_posix()] = dict(checked=checked, mismatches=errors, registrySha256=sha(registry))
    return result


def audit(root):
    root = Path(root).resolve()
    repositories = {}
    for name in ("diss", "MqttBenchmark", "mqtty"):
        directory = root / name
        files = [p for p in command("git", "-c", "core.quotepath=false", "ls-files", "-z", cwd=directory).split("\0") if p]
        repositories[name] = dict(commit=command("git", "rev-parse", "HEAD", cwd=directory),
            workingTree=command("git", "status", "--porcelain", cwd=directory), trackedFiles=len(files),
            csharpFiles=sum(p.endswith(".cs") for p in files), texFiles=sum(p.endswith(".tex") for p in files),
            markdownFiles=sum(p.endswith(".md") for p in files),
            projects=[p for p in files if p.endswith(".csproj")],
            textSourceSha256={p: sha(directory / p) for p in files
                if Path(p).suffix.lower() in (".tex", ".bib", ".md", ".cs", ".csproj", ".sln", ".py", ".ps1") and (directory / p).is_file()})
    return dict(method="Static reachable-TeX and tracked-source audit; no compiler and no new load", auditorSha256=sha(__file__),
                metaCommit=command("git", "rev-parse", "HEAD", cwd=root), repositories=repositories,
                latex=inspect_tex(root / "diss"), publicationRegistries=inspect_publications(root),
                localBuildTools={tool: shutil.which(tool) for tool in ("xelatex", "latexmk", "biber", "pandoc", "dotnet")})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = audit(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(dict(texFiles=len(data["latex"]["reachableFiles"]), duplicateLabels=data["latex"]["duplicateLabels"],
        unresolvedCitationKeys=data["latex"]["unresolvedCitationKeys"], unresolvedReferences=data["latex"]["unresolvedReferences"],
        missingImages=[p for p in data["latex"]["images"] if not p["resolved"]], publicationRegistries=data["publicationRegistries"]), ensure_ascii=False))
