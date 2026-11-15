"""Read four registered historical ZIPs without extraction or simulation."""
import argparse
from datetime import datetime, timezone
import hashlib
import itertools
import json
from pathlib import Path, PurePosixPath
import stat
import sys
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from control_manifest_audit import BROKERS, STAGES, EXPECTED_COMMIT, SHA256


KEYS = frozenset(f"m{m}.q{q}.p{p}.s{s}" for m, q, p, s in
                 itertools.product((16, 256), range(3), (1, 16, 64, 128), (42, 43, 44)))
MAX_JSON_BYTES = 8 * 1024 * 1024


def digest_stream(stream):
    stream.seek(0)
    value = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        value.update(block)
    stream.seek(0)
    return value.hexdigest()


def read_json_member(bundle, name):
    info = bundle.getinfo(name)
    if info.file_size > MAX_JSON_BYTES:
        raise ValueError("Oversized JSON member: " + name)
    raw = bundle.read(name)
    return raw, json.loads(raw.decode("utf-8-sig"))


def inspect_archive(entry, broker, *, expected_keys=KEYS):
    """Hash bytes before and after ZIP reads; reject ambiguous member identities."""
    path = Path(entry["path"])
    expected_sha = entry["expectedSha256"]
    if not isinstance(expected_sha, str) or not SHA256.fullmatch(expected_sha):
        raise ValueError("Invalid registered archive SHA-256")
    records = []
    with path.open("rb") as source:
        archive_sha = digest_stream(source)
        if archive_sha != expected_sha.lower():
            raise ValueError("Archive SHA-256 mismatch: " + str(path))
        with ZipFile(source, "r") as bundle:
            members, manifests = set(), {}
            for info in bundle.infolist():
                name = info.filename
                parts = PurePosixPath(name).parts
                if (name in members or "\\" in name or name.startswith("/") or
                        ".." in parts or not parts or ":" in parts[0] or
                        "//" in name or "/./" in name or name.startswith("./")):
                    raise ValueError("Duplicate or unsafe ZIP member: " + name)
                members.add(name)
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError("Symlink ZIP member: " + name)
                if info.is_dir():
                    continue
                if len(parts) != 3 or parts[0] not in STAGES or parts[1] not in expected_keys:
                    raise ValueError("Unexpected ZIP member layout: " + name)
                if parts[2] == "experiment-manifest.json":
                    manifests[(parts[0], parts[1])] = name
            expected = set(itertools.product(STAGES, expected_keys))
            if set(manifests) != expected:
                raise ValueError("Incomplete or unexpected manifest matrix: " + broker)
            for (stage, key), name in sorted(manifests.items()):
                prefix = str(PurePosixPath(name).parent)
                config_name = prefix + "/config.json"
                if config_name not in members:
                    raise ValueError("Missing config.json: " + prefix)
                raw, manifest = read_json_member(bundle, name)
                config_raw, _ = read_json_member(bundle, config_name)
                provenance = manifest["provenance"]
                if provenance.get("mqttYCommitSha") != EXPECTED_COMMIT:
                    raise ValueError("Unexpected source commit: " + name)
                profiles = provenance.get("brokerProfiles", [])
                if len(profiles) != 1:
                    raise ValueError("Expected exactly one broker profile: " + name)
                binary = {}
                for source_fields in (manifest, provenance):
                    for field, value in source_fields.items():
                        if any(token in field.lower() for token in ("binary", "assembly", "executable", "dll")):
                            if field in binary and binary[field] != value:
                                raise ValueError("Conflicting binary identity fields: " + field)
                            binary[field] = value
                record = {
                    "broker": broker, "stage": stage, "key": key,
                    "manifestSha256": hashlib.sha256(raw).hexdigest(),
                    "configSha256": hashlib.sha256(config_raw).hexdigest(),
                    "runId": manifest["runId"], "createdAtUtc": manifest["createdAtUtc"],
                    "sourceCommitSha": provenance["mqttYCommitSha"],
                    "profileSha256": provenance["brokerProfileSha256"],
                    "referenceObservationsSha256": provenance.get("referenceObservationsSha256"),
                    "profileCalibrationObservationsSha256": profiles[0]["provenance"]["observationsSha256"],
                    "binaryIdentity": binary, "archivePath": str(path), "archiveSha256": archive_sha,
                }
                for field in ("profileSha256", "profileCalibrationObservationsSha256"):
                    if not isinstance(record[field], str) or not SHA256.fullmatch(record[field]):
                        raise ValueError("Invalid provenance hash: " + field)
                if str(profiles[0].get("sha256", "")).lower() != record["profileSha256"].lower():
                    raise ValueError("Inconsistent profile hashes: " + name)
                reference = record["referenceObservationsSha256"]
                if stage == "holdout" and (not isinstance(reference, str) or not SHA256.fullmatch(reference)):
                    raise ValueError("Missing or invalid holdout observations hash: " + name)
                records.append(record)
        if digest_stream(source) != archive_sha:
            raise ValueError("Archive changed during reading: " + str(path))
        size = source.seek(0, 2)
    return {"path": str(path), "sha256": archive_sha, "bytes": size,
            "manifests": len(records)}, records


def export_registry(registry, *, expected_keys=KEYS):
    names = {f"{broker}-model-evidence.zip": broker for broker in BROKERS}
    selected = {}
    for entry in registry["compactArchives"]:
        name = entry.get("name")
        if name not in names:
            continue
        if name in selected:
            raise ValueError("Duplicate registered archive: " + name)
        selected[name] = entry
    if set(selected) != set(names):
        raise ValueError("Exactly four named broker model archives are required")
    archives, records = [], []
    for name in sorted(selected):
        archive, rows = inspect_archive(selected[name], names[name], expected_keys=expected_keys)
        archives.append(archive)
        records.extend(rows)
    return {"observedAtUtc": datetime.now(timezone.utc).isoformat(), "archives": archives,
            "records": records,
            "scope": "Read-only archive metadata and exact member hashes; no simulation or extraction"}


def export_to_file(registry_path, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError("Output exists; choose a new observation path: " + str(output))
    registry = json.loads(Path(registry_path).read_text(encoding="utf-8-sig"))
    result = export_registry(registry)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = export_to_file(args.archive_registry, args.output)
    print(json.dumps({"archives": len(result["archives"]), "manifests": len(result["records"])}))


if __name__ == "__main__":
    main()
