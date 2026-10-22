"""Archive immutable evidence outside its source directory and verify every file by SHA-256."""
import argparse
import hashlib
import json
import os
import tarfile
from pathlib import Path, PurePosixPath


def digest(stream):
    return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(path, root_name, expected):
    actual = {}
    with tarfile.open(path, "r|gz") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or name.parts[0] != root_name or not member.isfile():
                raise ValueError("Unexpected archive member: " + member.name)
            relative = str(name.relative_to(root_name))
            if relative in actual:
                raise ValueError("Duplicate archive member")
            actual[relative] = digest(archive.extractfile(member))
    if actual != expected:
        raise ValueError("Archive differs from the source evidence; sources retained")


def create(work, target):
    work, target = Path(work).resolve(strict=True), Path(target).resolve()
    if not work.is_dir() or target.is_relative_to(work) or target.exists():
        raise ValueError("Choose a new archive path outside the evidence directory")
    files = sorted(work.rglob("*"))
    if any(p.is_symlink() for p in files):
        raise ValueError("Evidence must not contain symbolic links")
    files = [p for p in files if p.is_file()]
    expected = {}
    for p in files:
        with p.open("rb") as stream:
            expected[p.relative_to(work).as_posix()] = digest(stream)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    with partial.open("xb") as output:
        with tarfile.open(fileobj=output, mode="w:gz", compresslevel=1) as archive:
            for p in files:
                archive.add(p, arcname=work.name + "/" + p.relative_to(work).as_posix(), recursive=False)
    verify(partial, work.name, expected)
    # A hard link publishes without ever overwriting an existing archive on the same filesystem.
    os.link(partial, target)
    partial.unlink()
    with target.open("rb") as stream:
        archive_hash = digest(stream)
    receipt = dict(archiveSha256=archive_hash, bytes=target.stat().st_size,
                   allMembersVerified=True, verifiedFileMembers=len(expected), sourceCopiesRetained=True)
    target.with_name(target.name + ".receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(receipt))
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--archive-path", type=Path, required=True)
    args = parser.parse_args()
    create(args.work_dir, args.archive_path)
