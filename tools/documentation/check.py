"""Check local documentation links and the immutable boundaries of the migration."""
import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layout import ROOT, configuration, sha


def local_links(text):
    # Command examples contain placeholder paths, not clickable documentation links.
    text = re.sub(r"(?ms)^```[^\n]*\n.*?^```[^\n]*$", "", text)
    links = re.findall(r"!?\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)\)", text)
    links += re.findall(r'(?:href|src)=[\'"]([^\'"]+)[\'"]', text)
    for value in links:
        value = value.removeprefix("<").removesuffix(">")
        if value.startswith(("#", "/", "\\")) or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
            continue
        yield unquote(value.split("#", 1)[0].split("?", 1)[0])


def inspect():
    sources = [ROOT / "README.md", ROOT / "MqttBenchmark/README.md", ROOT / "mqtty/README.md", ROOT / "diss/doc/README.md"]
    for folder in (ROOT / "docs", ROOT / "reports"):
        sources.extend(folder.rglob("*.md"))
        sources.extend(folder.rglob("*.html"))
    failures = []
    checked = 0
    for path in sorted(set(sources)):
        if not path.is_file():
            failures.append({"source": path.relative_to(ROOT).as_posix(), "target": "<document missing>"})
            continue
        for target in local_links(path.read_text(encoding="utf-8-sig")):
            checked += 1
            if not (path.parent / target).exists():
                failures.append({"source": path.relative_to(ROOT).as_posix(), "target": target})
    manifest_path = ROOT / "reports/data/documentation-layout-v1/manifest.json"
    boundaries = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key in ("protectedDissertationFilesSha256", "unchangedScientificFilesSha256"):
            invalid = [name for name, digest in manifest[key].items() if not (ROOT / name).is_file() or sha(ROOT / name) != digest]
            boundaries[key] = {"checked": len(manifest[key]), "changed": invalid}
    return {"documents": len(set(sources)), "localLinksChecked": checked, "brokenLinks": failures, "immutableBoundaries": boundaries}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    value = inspect()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False))
    if value["brokenLinks"] or any(v["changed"] for v in value["immutableBoundaries"].values()):
        raise SystemExit(1)
