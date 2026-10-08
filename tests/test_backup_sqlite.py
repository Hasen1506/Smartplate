"""The operator backup must capture a live database and be restorable."""
import sqlite3

import pytest

from scripts.backup_sqlite import backup

pytestmark = pytest.mark.sqlite_only      # about the SQLite file / sqlite-only settings


def test_backup_is_consistent_and_refuses_overwrite(tmp_path):
    source = tmp_path / "live.db"
    destination = tmp_path / "snapshot.db"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE orders(id TEXT PRIMARY KEY, state TEXT)")
        db.execute("INSERT INTO orders VALUES('o-1','unknown')")
    backup(source, destination)
    with sqlite3.connect(destination) as saved:
        assert saved.execute("SELECT state FROM orders WHERE id='o-1'").fetchone() == ("unknown",)
        assert saved.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    with pytest.raises(FileExistsError):
        backup(source, destination)
