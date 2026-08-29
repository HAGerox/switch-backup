import sqlite3
from pathlib import Path

from switchbackup.storage import Database


class FakeSecrets:
    def __init__(self):
        self.values = {}

    def set(self, credential_id, kind, value):
        self.values[(credential_id, kind)] = value

    def get(self, credential_id, kind):
        return self.values.get((credential_id, kind), "")

    def delete(self, credential_id, kind):
        self.values.pop((credential_id, kind), None)


def test_database_round_trip(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3", secrets=FakeSecrets())
    cred = db.add_credential("Main", "admin", "pw")
    assert db.get_credential_password(cred.id) == "pw"

    assert db.add_switches(["10.0.0.201", "10.0.0.202"]) == 2
    switches = db.list_switches()
    assert [s.ip for s in switches] == ["10.0.0.201", "10.0.0.202"]

    db.update_switch_discovery(
        switches[0].id, cred.id, "cisco_ios", "Core-Switch", "C9300-48P"
    )
    updated = db.list_switches()[0]
    assert updated.name == "Core-Switch"
    assert updated.model == "C9300-48P"
    assert updated.last_credential_id == cred.id
    assert updated.last_device_type == "cisco_ios"

    assert db.update_switch(updated.id, "10.0.0.210", "Control Room") is True
    edited = next(item for item in db.list_switches() if item.id == updated.id)
    assert edited.ip == "10.0.0.210"
    assert edited.name == "Control Room"
    assert edited.model == ""
    assert edited.last_credential_id is None
    assert edited.last_device_type is None


def test_existing_database_gets_model_column(tmp_path: Path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                username TEXT NOT NULL
            );
            CREATE TABLE switches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL DEFAULT '',
                last_credential_id INTEGER NULL,
                last_device_type TEXT NULL
            );
            INSERT INTO switches(ip, name) VALUES('10.0.0.201', 'Old Switch');
            """
        )

    db = Database(path, secrets=FakeSecrets())
    switch = db.list_switches()[0]
    assert switch.model == ""
