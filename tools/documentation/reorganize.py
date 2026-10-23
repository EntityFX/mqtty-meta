"""Apply the reviewed documentation map; never change dissertation files outside doc/."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layout import ROOT, configuration, path_map, refresh_report_registries, retarget, sha


def tracked(repository):
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=repository).decode("utf-8")
    return [repository / p for p in output.split("\0") if p]


def text_sources():
    paths = {ROOT / "README.md"}
    for name in ("docs", "reports", "tools"):
        paths.update((ROOT / name).rglob("*.md"))
    paths.update((ROOT / "reports").rglob("*.html"))
    for name in ("MqttBenchmark", "mqtty"):
        paths.update(p for p in tracked(ROOT / name) if p.suffix.lower() == ".md")
    paths.update((ROOT / "diss/doc").rglob("*.md"))
    return sorted(p for p in paths if p.is_file() and "__pycache__" not in p.parts)


def main():
    config = configuration()
    mapping = path_map(ROOT)
    moves = {ROOT / old: ROOT / new for old, new in config["moves"].items()}
    removed = {ROOT / old for old in config["removedReasons"]}
    destination = ROOT / "reports/data/documentation-layout-v1"
    if destination.exists():
        raise ValueError("Migration already applied; use Git history and layout.py for later publications")
    sources = {p: p.read_text(encoding="utf-8-sig") for p in text_sources()}
    # Explicit files only: all targets must remain within this workspace.
    for old, new in moves.items():
        if not old.is_file() or new.exists() or not new.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError("Unsafe or conflicting move: " + str(old))
    protected = {p.relative_to(ROOT).as_posix(): sha(p) for p in tracked(ROOT / "diss")
                 if p.is_file() and not p.is_relative_to(ROOT / "diss/doc")}
    scientific = {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / "reports/data").rglob("*")
                  if p.is_file() and p.suffix.lower() not in (".md", ".html") and p.name != "publication-sha256.json"}
    registries = [ROOT / "reports/publication-sha256.json", *(ROOT / "reports/data").glob("*/publication-sha256.json")]
    previous = {p.relative_to(ROOT).as_posix(): json.loads(p.read_text(encoding="utf-8-sig")) for p in registries}
    records = [{"source": p.relative_to(ROOT).as_posix(), "destination": q.relative_to(ROOT).as_posix(), "sourceSha256": sha(p)}
               for p, q in moves.items()]
    obsolete = [{"path": p.relative_to(ROOT).as_posix(), "sha256": sha(p),
                 "replacement": config["replacements"][p.relative_to(ROOT).as_posix()],
                 "reason": config["removedReasons"][p.relative_to(ROOT).as_posix()]} for p in sorted(removed)]
    for old, new in moves.items():
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old), str(new))
    for old in removed:
        old.unlink()
    for old, text in sources.items():
        if old in removed:
            continue
        new = moves.get(old, old)
        updated = retarget(text, old, new, mapping)
        # Plain repository paths in commands/prose are distinct from links.
        for source, target in sorted(config["moves"].items(), key=lambda item: -len(item[0])):
            updated = updated.replace(source, target)
        new.write_text(updated, encoding="utf-8", newline="\n")
    for old, new in moves.items():
        if old.is_relative_to(ROOT / "docs/examples") and new.suffix == ".json":
            value = json.loads(new.read_text(encoding="utf-8-sig"))
            def adjust(node):
                if isinstance(node, dict):
                    for key, item in node.items():
                        if key == "brokerProfilePath" and isinstance(item, str) and not Path(item).is_absolute():
                            import os
                            target = (old.parent / item).resolve()
                            node[key] = Path(os.path.relpath(mapping.get(target, target), new.parent)).as_posix()
                        else:
                            adjust(item)
                elif isinstance(node, list):
                    for item in node:
                        adjust(item)
            adjust(value)
            new.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    if any(not (ROOT / p).is_file() or sha(ROOT / p) != digest for p, digest in protected.items()):
        raise ValueError("A protected dissertation file changed")
    if any(sha(ROOT / p) != digest for p, digest in scientific.items()):
        raise ValueError("Scientific data changed")
    destination.mkdir(parents=True)
    manifest = {"version": 1, "metaCommitBeforeMigration": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "moves": records, "removedObsoleteGuides": obsolete,
                "protectedDissertationFilesSha256": protected, "unchangedScientificFilesSha256": scientific,
                "previousPublicationRegistries": previous,
                "scope": "Documentation/navigation only; original scientific files and dissertation outside doc/ unchanged"}
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    refresh_report_registries(ROOT / "reports")
    print(json.dumps({"moved": len(moves), "removedObsolete": len(removed), "protectedDissertationFiles": len(protected),
                      "unchangedScientificFiles": len(scientific)}))


if __name__ == "__main__":
    main()
