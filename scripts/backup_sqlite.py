"""Make a consistent SQLite snapshot for transfer to external backup storage.

Usage: python scripts/backup_sqlite.py /secure/off-host-staging/smartplate.db
The destination contains encrypted Swiggy tokens and private profile data;
restrict access and encrypt it before moving it off-host.
"""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3


def backup(source: Path, destination: Path) -> None:
    source, destination = source.resolve(), destination.resolve()
    if source == destination or not source.is_file():
        raise ValueError("Source must exist and differ from the destination")
    if not destination.parent.is_dir():
        raise ValueError("Destination directory must already exist")
    if destination.exists():
        raise FileExistsError(destination)
    temporary = destination.with_name(destination.name + ".partial")
    if temporary.exists():
        raise FileExistsError(temporary)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as current:
            with closing(sqlite3.connect(temporary)) as saved:
                current.backup(saved)
                if saved.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup failed integrity check")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    backup(Path(os.environ.get("SMARTPLATE_DB", "smartplate.db")), args.destination)
    print(f"Verified SQLite backup: {args.destination}")
