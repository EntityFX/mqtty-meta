"""Read-only, reproducible lexical audit of a TeX root and its bibliography.

This is deliberately not a TeX interpreter or a replacement for a biber build.
Conditional branches are traversed conservatively; package lookup and expansion
of arbitrary macros are not performed. Locations always refer to original lines.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re


COMMAND = re.compile(r"\\([A-Za-z@]+|.)(\*)?", re.S)
CITES = set("cite autocite parencite textcite footcite smartcite supercite citeauthor citetitle citeyear citeyearpar fullcite footfullcite citep citet Citep Citet Citeauthor nocite".split())
REFS = set("ref pageref eqref autoref nameref vref Vref cref Cref cpageref Cpageref labelcref labelcpageref subref".split())
RANGES = {"crefrange", "Crefrange", "cpagerefrange", "Cpagerefrange"}
DEFINITIONS = {"newcommand", "renewcommand", "providecommand", "DeclareRobustCommand"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def blank(text):
    return "".join("\n" if c == "\n" else " " for c in text)


def lexical_text(text):
    """Blank comments and verbatim constructs while retaining byte-character offsets."""
    result = list(text)
    i = 0
    while i < len(text):
        if text[i] == "%":
            end = text.find("\n", i)
            end = len(text) if end < 0 else end
            result[i:end] = blank(text[i:end])
            i = end
        elif text[i] == "\\":
            match = COMMAND.match(text, i)
            if not match:
                i += 1
                continue
            if match[1] == "verb" and match.end() < len(text):
                delim = text[match.end()]
                end = text.find(delim, match.end() + 1)
                end = len(text) if end < 0 else end + 1
                result[i:end] = blank(text[i:end])
                i = end
                continue
            env = re.match(r"\\begin\{(verbatim\*?|lstlisting|minted|comment)\}", text[i:])
            if env:
                endtag = "\\end{" + env[1] + "}"
                end = text.find(endtag, i + env.end())
                end = len(text) if end < 0 else end + len(endtag)
                result[i:end] = blank(text[i:end])
                i = end
                continue
            i = match.end()  # escaped percent is consumed as a control symbol
        else:
            i += 1
    return "".join(result)


def space(text, pos):
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def group(text, pos, opener="{", closer="}"):
    pos = space(text, pos)
    if pos >= len(text) or text[pos] != opener:
        return None
    start, depth, i = pos, 1, pos + 1
    while i < len(text):
        if text[i] == "\\":
            match = COMMAND.match(text, i)
            i = match.end() if match else i + 1
            continue
        if text[i] == opener:
            depth += 1
        elif text[i] == closer:
            depth -= 1
            if not depth:
                return text[start + 1:i], i + 1
        i += 1
    raise ValueError(f"Unclosed {opener} group at line {text.count(chr(10), 0, start) + 1}")


def arguments(text, pos, count=1):
    pos = space(text, pos)
    while pos < len(text) and text[pos] == "[":
        optional = group(text, pos, "[", "]")
        pos = space(text, optional[1])
    values = []
    for _ in range(count):
        item = group(text, pos)
        if item is None:
            return None
        values.append(item[0])
        pos = item[1]
    return values, pos


def without_definitions(text):
    """Exclude literal macro templates from actual citation/reference occurrences.

    Supports common newcommand and primitive def forms. Bodies are still scanned
    separately for include/bibliography candidates, with explicit conservative scope.
    """
    result = list(text)
    for match in COMMAND.finditer(text):
        if match[1] not in DEFINITIONS | {"def", "gdef", "edef", "xdef"}:
            continue
        pos = space(text, match.end())
        name = group(text, pos)
        if name:
            pos = name[1]
        else:
            command = COMMAND.match(text, pos)
            if not command:
                continue
            pos = command.end()
        if match[1] in DEFINITIONS:
            parsed = arguments(text, pos)
            if parsed:
                result[match.start():parsed[1]] = blank(text[match.start():parsed[1]])
        else:
            while pos < len(text) and text[pos] != "{":
                pos += 1
            body = group(text, pos)
            if body:
                result[match.start():body[1]] = blank(text[match.start():body[1]])
    return "".join(result)


def location(path, text, pos, root):
    return {"path": path.relative_to(root).as_posix(), "line": text.count("\n", 0, pos) + 1}


def split_keys(value):
    return [key.strip() for key in value.split(",") if key.strip()]


def parse_bib(path, root):
    """Parse entries and fields; balanced braces/quoted strings and # concatenation."""
    text = lexical_text(path.read_text(encoding="utf-8-sig"))
    entries, errors = [], []
    pos = 0
    while True:
        found = re.search(r"@([A-Za-z]+)\s*([{(])", text[pos:])
        if not found:
            break
        start = pos + found.start()
        opening = pos + found.end() - 1
        kind = found[1].lower()
        try:
            body = group(text, opening, found[2], "}" if found[2] == "{" else ")")
        except ValueError as exc:
            errors.append({**location(path, text, start, root), "error": str(exc)})
            break
        pos = body[1]
        if kind in {"comment", "preamble", "string"}:
            continue
        content = body[0]
        comma = content.find(",")
        if comma < 0 or not content[:comma].strip():
            errors.append({**location(path, text, start, root), "error": "Entry has no key/comma"})
            continue
        key = content[:comma].strip()
        entry = {"key": key, "type": kind, **location(path, text, start, root), "fields": {}, "fieldLocations": {}}
        p = comma + 1
        while p < len(content):
            p = space(content, p)
            if p < len(content) and content[p] == ",":
                p += 1
                continue
            if p == len(content):
                break
            field = re.match(r"([A-Za-z][A-Za-z0-9_-]*)\s*=\s*", content[p:])
            if not field:
                errors.append({**location(path, text, opening + 1 + p, root), "key": key,
                               "error": "Expected field = value", "fragment": content[p:p + 80]})
                break
            name = field[1].lower()
            field_pos = opening + 1 + p
            p += field.end()
            pieces = []
            while True:
                p = space(content, p)
                if p >= len(content):
                    errors.append({**location(path, text, field_pos, root), "key": key, "error": "Missing field value"})
                    break
                if content[p] == "{":
                    item = group(content, p)
                    pieces.append(item[0])
                    p = item[1]
                elif content[p] == '"':
                    end = p + 1
                    depth = 0
                    while end < len(content):
                        if content[end] == "\\":
                            end += 2
                            continue
                        if content[end] == "{":
                            depth += 1
                        elif content[end] == "}":
                            depth -= 1
                        elif content[end] == '"' and depth == 0:
                            break
                        end += 1
                    if end == len(content):
                        errors.append({**location(path, text, field_pos, root), "key": key, "error": "Unclosed quoted value"})
                        p = end
                        break
                    pieces.append(content[p + 1:end])
                    p = end + 1
                else:
                    scalar = re.match(r"[^\s,#]+", content[p:])
                    if not scalar:
                        errors.append({**location(path, text, field_pos, root), "key": key, "error": "Invalid field value"})
                        p = len(content)
                        break
                    pieces.append(scalar[0])
                    p += scalar.end()
                p = space(content, p)
                if p < len(content) and content[p] == "#":
                    p += 1
                    continue
                break
            if name in entry["fields"]:
                errors.append({**location(path, text, field_pos, root), "key": key, "error": "Duplicate field", "field": name})
            entry["fields"][name] = "".join(pieces)
            entry["fieldLocations"][name] = location(path, text, field_pos, root)
            if p < len(content) and content[p] != ",":
                errors.append({**location(path, text, opening + 1 + p, root), "key": key, "error": "Missing comma after field"})
                break
        entries.append(entry)
    return entries, errors


def audit(root, entrypoint="dissertation.tex"):
    root = Path(root).resolve()
    protected_before = {p.relative_to(root).as_posix(): sha(p)
                        for p in sorted(root.rglob("*")) if p.is_file() and p.suffix.lower() in {".tex", ".bib"}}
    files, edges, resources, missing, unresolved, cycles = {}, [], {}, [], [], []
    citations, nocites, labels, references = [], [], [], []
    syntax_errors = []

    def resolve(name, parent, suffix):
        candidate = Path(name)
        if not candidate.suffix:
            candidate = candidate.with_suffix(suffix)
        for base in (root, parent):
            full = (base / candidate).resolve()
            if full.is_relative_to(root) and full.is_file():
                return full
        return None

    def visit(path, chain):
        if path in chain:
            cycles.append([p.relative_to(root).as_posix() for p in chain + [path]])
            return
        if path in files:
            return
        original = path.read_text(encoding="utf-8-sig")
        files[path] = sha(path)
        text = lexical_text(original)
        try:
            body = without_definitions(text)
        except ValueError as exc:
            syntax_errors.append({"path": path.relative_to(root).as_posix(), "error": str(exc)})
            body = text
        for match in COMMAND.finditer(text):
            kind = match[1]
            if kind not in {"input", "include", "addbibresource", "bibliography", "includeonly"}:
                continue
            try:
                parsed = arguments(text, match.end())
            except ValueError as exc:
                syntax_errors.append({**location(path, text, match.start(), root), "error": str(exc)})
                continue
            if parsed is None and kind == "input":
                plain = re.match(r"\s+([^\s%{}]+)", text[match.end():])
                parsed = ([plain[1]], match.end() + plain.end()) if plain else None
            loc = location(path, text, match.start(), root)
            if parsed is None:
                unresolved.append({**loc, "command": kind, "argument": None})
                continue
            if kind == "includeonly":
                unresolved.append({**loc, "command": kind, "argument": parsed[0][0], "note": "includeonly not executed; whole graph audited"})
                continue
            for name in split_keys(parsed[0][0]):
                if re.search(r"[\\#]", name):
                    unresolved.append({**loc, "command": kind, "argument": name})
                    continue
                suffix = ".tex" if kind in {"input", "include"} else ".bib"
                target = resolve(name, path.parent, suffix)
                record = {**loc, "command": kind, "argument": name,
                          "target": target.relative_to(root).as_posix() if target else None}
                if suffix == ".bib":
                    resources.setdefault(name, []).append(record)
                else:
                    edges.append(record)
                if target is None:
                    missing.append(record)
                elif suffix == ".tex":
                    visit(target, chain + [path])
        for match in COMMAND.finditer(body):
            kind = match[1]
            if kind not in CITES | REFS | RANGES | {"label", "hyperref"}:
                continue
            try:
                if kind == "hyperref":
                    optional = group(body, match.end(), "[", "]")
                    parsed = ([optional[0]], optional[1]) if optional else None
                else:
                    parsed = arguments(body, match.end(), 2 if kind in RANGES else 1)
            except ValueError as exc:
                syntax_errors.append({**location(path, body, match.start(), root), "error": str(exc)})
                continue
            if not parsed:
                continue
            for value in parsed[0]:
                for key in split_keys(value):
                    rec = {**location(path, body, match.start(), root), "command": kind, "key": key}
                    if re.search(r"[\\#]", key):
                        unresolved.append(rec)
                    elif kind in CITES:
                        (nocites if kind == "nocite" else citations).append(rec)
                    elif kind == "label":
                        labels.append(rec)
                    else:
                        references.append(rec)

    visit(root / entrypoint, [])
    bibfiles = sorted({root / rec["target"] for rs in resources.values() for rec in rs if rec["target"]})
    before = {p.relative_to(root).as_posix(): digest for p, digest in sorted(files.items())}
    before.update({p.relative_to(root).as_posix(): sha(p) for p in bibfiles})
    entries, bib_errors = [], []
    for path in bibfiles:
        parsed, errors = parse_bib(path, root)
        entries.extend(parsed)
        bib_errors.extend(errors)
    def indexed(records, field="key"):
        result = defaultdict(list)
        for rec in records:
            result[rec[field]].append(rec)
        return dict(sorted(result.items()))
    by_key, by_label = indexed(entries), indexed(labels)
    cited = {r["key"] for r in citations}
    no_cited = {r["key"] for r in nocites}
    by_doi, by_title = defaultdict(list), defaultdict(list)
    metadata_warnings = []
    for entry in entries:
        fields = entry["fields"]
        if fields.get("doi"):
            doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", fields["doi"], flags=re.I).strip().lower()
            by_doi[doi].append(entry)
            if not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
                metadata_warnings.append({"key": entry["key"], **entry["fieldLocations"]["doi"], "issue": "DOI syntax", "value": fields["doi"]})
        title = re.sub(r"[^\w]+", "", fields.get("title", "").lower())
        if title:
            by_title[title].append(entry)
        absent = [name for name in ("title",) if not fields.get(name)]
        if entry["type"] in {"article", "book", "inproceedings", "incollection"}:
            if not fields.get("year") and not fields.get("date"):
                absent.append("year/date")
            if not fields.get("author") and not fields.get("editor"):
                absent.append("author/editor")
        if absent:
            metadata_warnings.append({"key": entry["key"], "path": entry["path"], "line": entry["line"], "issue": "Missing basic fields (local heuristic)", "fields": absent})
    after = {name: sha(root / name) for name in before}
    protected_after = {p.relative_to(root).as_posix(): sha(p)
                       for p in sorted(root.rglob("*")) if p.is_file() and p.suffix.lower() in {".tex", ".bib"}}
    missing_citations = [r for r in citations + nocites if r["key"] != "*" and r["key"] not in by_key]
    missing_references = [r for r in references if r["key"] not in by_label]
    inherited = [{"key": e["key"], "field": f, "target": k, **e["fieldLocations"][f]}
                 for e in entries for f in ("crossref", "xref", "xdata") if f in e["fields"]
                 for k in split_keys(e["fields"][f]) if k not in by_key]
    return {"schemaVersion": 1, "entrypoint": entrypoint,
            "scope": {"method": "conservative lexical traversal, not TeX execution", "conditionalBranches": "both",
                      "macroDefinitions": "not expanded; literal cite/ref templates excluded", "uniqueFilesNotExpansionVisits": True,
                      "bibliography": "union of literal resource declarations in reachable files", "engineBuildVerified": False,
                      "externalMetadataVerified": False},
            "counts": {"texFiles": len(files), "includeEdges": len(edges), "bibFiles": len(bibfiles),
                       "bibliographyEntries": len(entries), "uniqueBibliographyKeys": len(by_key),
                       "citationOccurrences": len(citations), "uniqueCitedKeys": len(cited), "nociteOccurrences": len(nocites),
                       "labelDefinitions": len(labels), "referenceOccurrences": len(references),
                       "missingCitations": len(missing_citations), "missingReferences": len(missing_references),
                       "missingOrdinaryCitations": sum(r["command"] != "nocite" for r in missing_citations),
                       "missingNociteKeys": sum(r["command"] == "nocite" for r in missing_citations),
                       "duplicateKeys": sum(len(v) > 1 for v in by_key.values()),
                       "duplicateLabels": sum(len(v) > 1 for v in by_label.values()),
                       "bibParseErrors": len(bib_errors), "texParseErrors": len(syntax_errors)},
            "includeGraph": edges, "missingInputs": missing, "unresolvedLexicalConstructs": unresolved, "includeCycles": cycles,
            "bibResources": resources, "entries": entries, "citations": citations, "nocites": nocites,
            "labels": labels, "references": references, "missingCitations": missing_citations,
            "missingReferences": missing_references, "duplicateKeys": {k: v for k, v in by_key.items() if len(v) > 1},
            "duplicateLabels": {k: v for k, v in by_label.items() if len(v) > 1},
            "duplicateDoiCandidates": {k: v for k, v in sorted(by_doi.items()) if len(v) > 1},
            "duplicateTitleCandidates": {k: v for k, v in sorted(by_title.items()) if len(v) > 1},
            "unusedKeys": sorted(set(by_key) - cited - no_cited), "nociteAll": "*" in no_cited,
            "missingInheritance": inherited, "metadataWarnings": metadata_warnings,
            "bibParseErrors": bib_errors, "texParseErrors": syntax_errors,
            "reachableSources": {"sha256Before": before, "sha256After": after, "unchanged": before == after},
            "protectedSources": {"scope": "all .tex and .bib under root, including unreachable drafts",
                                 "sha256Before": protected_before, "sha256After": protected_after,
                                 "unchanged": protected_before == protected_after}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="diss")
    parser.add_argument("--entrypoint", default="dissertation.tex")
    parser.add_argument("--output", required=True, help="New output JSON; never overwrite")
    args = parser.parse_args()
    output = Path(args.output)
    result = audit(args.root, args.entrypoint)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps(result["counts"], ensure_ascii=False, sort_keys=True))
    if not result["protectedSources"]["unchanged"]:
        raise SystemExit("Protected input changed during audit")


if __name__ == "__main__":
    main()
