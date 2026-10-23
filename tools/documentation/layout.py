"""Canonical documentation/report paths and relocation of generated report links."""
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[2]
CONFIG = Path(__file__).with_name("layout.json")


def configuration():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def path_map(root):
    config = configuration()
    combined = dict(config["moves"])
    combined.update(config["replacements"])
    return {(root / old).resolve(): (root / new).resolve() for old, new in combined.items()}


def retarget(text, source, destination, mapping):
    """Preserve resolved targets when a Markdown/HTML document changes location."""
    source, destination = Path(source).resolve(), Path(destination).resolve()

    def target(value):
        angle = value.startswith("<") and value.endswith(">")
        raw = value[1:-1] if angle else value
        if not raw or raw.startswith(("#", "/", "\\")) or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", raw):
            return value
        path, marker, fragment = raw.partition("#")
        path, query_mark, query = path.partition("?")
        old = (source.parent / unquote(path)).resolve()
        new = mapping.get(old, old)
        relative = Path(os.path.relpath(new, destination.parent)).as_posix()
        if query_mark:
            relative += "?" + query
        if marker:
            relative += "#" + fragment
        return "<" + relative + ">" if angle else relative

    text = re.sub(r"(!?\[[^\]\n]*\]\()(<[^>\n]+>|[^\s)]+)(\))",
                  lambda m: m[1] + target(m[2]) + m[3], text)
    text = re.sub(r'((?:href|src)=)([\'\"])([^\'\"]+)(\2)',
                  lambda m: m[1] + m[2] + target(m[3]) + m[4], text)
    return text


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def refresh_report_registries(reports):
    """Reseal document paths; reject any unexplained change to scientific files."""
    reports = Path(reports).resolve()
    mapping = configuration()["moves"]
    registries = sorted((reports / "data").glob("*/publication-sha256.json"))
    top = reports / "publication-sha256.json"
    if top.exists():
        registries.append(top)
    for registry in registries:
        value = json.loads(registry.read_text(encoding="utf-8-sig"))
        files = value.get("files", value)
        updated = {}
        for relative, previous in files.items():
            mapped = mapping.get("reports/" + relative, "reports/" + relative).removeprefix("reports/")
            path = reports / mapped
            if not path.is_file():
                raise ValueError("Publication member missing after relocation: " + mapped)
            actual = sha(path)
            if actual != previous and path.suffix.lower() not in (".md", ".html") and path.name != "publication-sha256.json":
                raise ValueError("Scientific file changed during documentation relocation: " + mapped)
            updated[mapped] = actual
        if "files" in value:
            value["files"] = updated
        else:
            value = updated
        registry.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def organize_reports(reports):
    """Let existing publishers emit their documents, then apply the canonical layout."""
    reports = Path(reports).resolve()
    mapping = path_map(reports.parent)
    moved = []
    for old, new in configuration()["moves"].items():
        if not old.startswith("reports/") or Path(old).suffix.lower() not in (".md", ".html"):
            continue
        source, destination = reports / old.removeprefix("reports/"), reports / new.removeprefix("reports/")
        if not source.is_file() or source == destination:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        text = retarget(source.read_text(encoding="utf-8-sig"), source, destination, mapping)
        destination.write_text(text, encoding="utf-8", newline="\n")
        source.unlink()
        moved.append(destination.relative_to(reports).as_posix())
    refresh_report_registries(reports)
    return moved
