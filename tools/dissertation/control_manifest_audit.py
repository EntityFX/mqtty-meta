"""Compare compact historical manifest exports with published model evidence.

This reads local JSON only. Archive and binary bytes are not opened: a consistent
export establishes metadata agreement, not independent execution attestation.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re


BROKERS = ("Aedes", "Mosquitto", "ActiveMQ", "EMQX")
STAGES = ("fidelity", "holdout", "relay")
EXPECTED_COMMIT = "115885e012053d0112413e07743b061369ebea93"
SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def audit(export, model_evidence, *, expected_commit=EXPECTED_COMMIT,
          expected_per_stage=72, profile_metadata=None):
    """Return explicit discrepancies; missing binary attestations are limitations."""
    records = export if isinstance(export, list) else export.get("records", export.get("rows"))
    if not isinstance(records, list):
        raise ValueError("Export must contain a records or rows array")
    errors, counts, seen, binary_identities = [], Counter(), set(), []
    archive_catalog = None if isinstance(export, list) else export.get("archives")
    archive_hashes = ({a["path"]: a["sha256"].lower() for a in archive_catalog}
                      if archive_catalog is not None else None)
    published = {}
    for broker in BROKERS:
        for row in model_evidence.get(broker, []):
            if row.get("stage") not in STAGES:
                continue
            identity = (broker, row.get("stage"), row.get("key"))
            if identity in published:
                errors.append({"identity": list(identity), "field": "publishedDuplicate"})
            published[identity] = row
    missing_binary = 0
    for record in records:
        if not isinstance(record, dict):
            errors.append({"field": "recordType"})
            continue
        identity = tuple(record.get(k) for k in ("broker", "stage", "key"))
        label = {"identity": list(identity)}
        if identity in seen:
            errors.append(dict(label, field="duplicateRecord"))
        seen.add(identity)
        counts[identity[:2]] += 1
        row = published.get(identity)
        if row is None:
            errors.append(dict(label, field="unpublishedRecord"))
            continue

        def compare(field, expected):
            actual = record.get(field)
            equal = (actual.lower() == expected.lower()
                     if field.endswith("Sha256") and isinstance(actual, str) and isinstance(expected, str)
                     else actual == expected)
            if not equal:
                errors.append(dict(label, field=field, expected=expected,
                                   actual=record.get(field)))

        compare("configSha256", row.get("configSha256"))
        compare("manifestSha256", row.get("filesSha256", {}).get("experiment-manifest.json"))
        compare("runId", row.get("directory", "").rstrip("/").rsplit("/", 1)[-1])
        compare("sourceCommitSha", expected_commit)
        profiles = row.get("profileHashes", [])
        if len(profiles) != 1 or str(record.get("profileSha256")).lower() != profiles[0].lower():
            errors.append(dict(label, field="profileSha256", expected=profiles,
                               actual=record.get("profileSha256")))
        for field in ("configSha256", "manifestSha256", "profileSha256", "archiveSha256"):
            if not isinstance(record.get(field), str) or not SHA256.fullmatch(record[field]):
                errors.append(dict(label, field=field + "Format"))
        if not record.get("archivePath"):
            errors.append(dict(label, field="archivePathMissing"))
        if archive_hashes is not None:
            if (archive_hashes.get(record.get("archivePath")) !=
                    str(record.get("archiveSha256")).lower()):
                errors.append(dict(label, field="archiveCatalogMismatch"))
        if not record.get("createdAtUtc"):
            errors.append(dict(label, field="createdAtUtcMissing"))
        metadata = (profile_metadata or {}).get(identity[0])
        if metadata:
            compare("profileCalibrationObservationsSha256", metadata["calibrationObservationsSha256"])
            if identity[1] == "holdout" or (identity[1] == "fidelity" and
                                            record.get("referenceObservationsSha256") is not None):
                key = "calibrationObservationsSha256" if identity[1] == "fidelity" else "validationObservationsSha256"
                compare("referenceObservationsSha256", metadata[key])
        binary = record.get("binaryIdentity")
        if binary in (None, {}, [], ""):
            missing_binary += 1
        else:
            binary_identities.append(dict(label, recordedIdentity=binary))
    for broker in BROKERS:
        for stage in STAGES:
            count = counts[(broker, stage)]
            if count != expected_per_stage:
                errors.append({"identity": [broker, stage], "field": "coverage",
                               "expected": expected_per_stage, "actual": count})
    for identity in sorted(set(published) - seen):
        errors.append({"identity": list(identity), "field": "missingRecord"})
    return {
        "status": "metadataConsistent" if not errors else "metadataMismatch",
        "recordCount": len(records), "publishedRecordCount": len(published),
        "counts": {b: {s: counts[(b, s)] for s in STAGES} for b in BROKERS},
        "errors": errors, "binaryIdentityMissingCount": missing_binary,
        "binaryIdentityRecords": binary_identities,
        "profileProvenanceCompared": bool(profile_metadata),
        "archiveBytesVerified": False, "binaryBuildOrExecutionVerified": False,
        "blindedValidationEstablished": False,
        "limitations": [
            "Manifest fields are compared from an export; original archive bytes were not reopened by this tool.",
            "A source commit or .NET version does not identify the executed application binary.",
            "Recorded binary identities, when present, are reported without independent attestation.",
            "Metadata consistency does not establish when developers first inspected control results.",
        ],
    }


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--model-evidence", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Output exists; choose a new receipt path")
    metadata = None
    if args.repository_root:
        data = args.repository_root / "reports/data"
        metadata = {b: {"calibrationObservationsSha256": sha(data / b / "calibration/observations.json"),
                        "validationObservationsSha256": sha(data / b / "validation/observations.json")}
                    for b in BROKERS}
    result = audit(json.loads(args.export.read_text(encoding="utf-8-sig")),
                   json.loads(args.model_evidence.read_text(encoding="utf-8-sig")),
                   profile_metadata=metadata)
    result["inputSha256"] = {"export": sha(args.export), "modelEvidence": sha(args.model_evidence)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({k: result[k] for k in ("status", "recordCount", "binaryIdentityMissingCount")}))
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
