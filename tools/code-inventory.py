"""Refresh the source-size tables in docs/overview/architecture.md using only Git and Python."""
import argparse
import hashlib
import json
import subprocess
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPOS = (".", "MqttBenchmark", "mqtty", "diss")
EXTENSIONS = {".cs", ".py", ".ps1", ".psm1", ".sh", ".js", ".axaml", ".xaml", ".lua"}
EXCLUDED = {"bin", "obj", "artifacts", "reports", "node_modules", "vendor", ".venv", "__pycache__", ".git"}
BEGIN = "<!-- code-inventory:start -->"
END = "<!-- code-inventory:end -->"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args]).decode("utf-8")


def collect():
    modules = {}
    revisions = {}
    source_hash = hashlib.sha256()
    layout = ROOT / "tools/documentation/layout.json"
    documentation_moves = json.loads(layout.read_text(encoding="utf-8"))["moves"] if layout.is_file() else {}
    for name in REPOS:
        repo = ROOT / name
        if git(repo, "rev-parse", "--show-toplevel").strip().replace("\\", "/").lower() != repo.resolve().as_posix().lower():
            raise RuntimeError(f"Initialize the submodule first: {name}")
        revisions[name] = git(repo, "rev-parse", "HEAD").strip()
        paths = sorted(set(filter(None, git(repo, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0"))))
        projects = sorted((Path(p) for p in paths if p.endswith(".csproj")), key=lambda p: len(p.parts), reverse=True)
        for item in paths:
            relative = Path(item)
            if relative.suffix.lower() not in EXTENSIONS or EXCLUDED.intersection(relative.parts):
                continue
            if relative.name.endswith((".g.cs", ".g.i.cs", ".Designer.cs")):
                continue
            path = repo / relative
            if not path.is_file():
                relocated = documentation_moves.get(path.relative_to(ROOT).as_posix())
                if relocated and (ROOT / relocated).is_file():
                    # A documented cross-repository move is counted at its new
                    # location, including before the source deletion is committed.
                    continue
                raise RuntimeError(f"Tracked source is missing: {path}")
            project = next((p for p in projects if relative.is_relative_to(p.parent)), None)
            test = any("test" in part.lower() for part in relative.parts)
            if project and relative.suffix.lower() in {".cs", ".axaml", ".xaml"}:
                module = project.stem
                directory = project.parent
                kind = "Тесты .NET" if "test" in project.stem.lower() else "Пример .NET" if "examples" in relative.parts else "Модуль .NET"
            else:
                directory = Path(*relative.parts[:min(2, len(relative.parts) - 1)]) if name == "." else Path(relative.parts[0])
                module = directory.as_posix()
                kind = "Тестовые скрипты" if test else "Скрипты"
            key = (name, module, kind)
            if key not in modules:
                modules[key] = dict(repository=name, module=module, kind=kind,
                                    directory=(Path(name) / directory).as_posix(), files=0,
                                    lines=0, nonblank=0, extensions=defaultdict(int))
            raw = path.read_bytes()
            lines = raw.decode("utf-8-sig").splitlines()
            entry = modules[key]
            entry["files"] += 1
            entry["lines"] += len(lines)
            entry["nonblank"] += sum(bool(line.strip()) for line in lines)
            entry["extensions"][relative.suffix.lower()] += 1
            # Normalize line endings for the source fingerprint across Git checkouts.
            source_hash.update((Path(name) / relative).as_posix().encode("utf-8") + b"\0")
            source_hash.update("\n".join(lines).encode("utf-8") + b"\0")
    entries = [modules[key] for key in sorted(modules)]
    return dict(method="physical-lines-v1", revisions=revisions,
                sourceSha256=source_hash.hexdigest(), modules=entries,
                totals={key: sum(m[key] for m in entries) for key in ("files", "lines", "nonblank")})


def render(data):
    lines = [BEGIN, "", "| Компонент / модуль | Вид | Файлов | Строк всего | Непустых строк |",
             "| --- | --- | ---: | ---: | ---: |"]
    for name in REPOS:
        entries = [m for m in data["modules"] if m["repository"] == name]
        title = "Корневой репозиторий" if name == "." else name
        lines.append(f"| **{title}** | **Итого** | **{sum(m['files'] for m in entries)}** | **{sum(m['lines'] for m in entries)}** | **{sum(m['nonblank'] for m in entries)}** |")
        for m in entries:
            lines.append(f"| [{m['module']}](../../{m['directory']}/) | {m['kind']} | {m['files']} | {m['lines']} | {m['nonblank']} |")
    total = data["totals"]
    lines.extend([f"| **Всего** | | **{total['files']}** | **{total['lines']}** | **{total['nonblank']}** |", "", END])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the saved inventory differs from the checkout")
    args = parser.parse_args()
    data = collect()
    doc = ROOT / "docs/overview/architecture.md"
    current = doc.read_text(encoding="utf-8")
    before, rest = current.split(BEGIN, 1)
    _, after = rest.split(END, 1)
    updated = before + render(data) + after
    inventory = ROOT / "docs/overview/code-inventory.json"
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if updated != current or not inventory.exists() or inventory.read_text(encoding="utf-8") != serialized:
            raise SystemExit("Inventory is stale; run python tools/code-inventory.py")
    else:
        doc.write_text(updated, encoding="utf-8", newline="\n")
        inventory.write_text(serialized, encoding="utf-8", newline="\n")
    print(json.dumps(data["totals"]))


if __name__ == "__main__":
    main()
