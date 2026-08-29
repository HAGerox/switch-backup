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


def test_sites_isolate_credentials_and_switches(tmp_path: Path):
    secrets = FakeSecrets()
    db = Database(tmp_path / "db.sqlite3", secrets=secrets)
    default_site = db.list_sites()[0]
    second_site = db.add_site("London Office")
    second_site = db.rename_site(second_site.id, "London Campus")

    first_credential = db.add_credential(
        "Main", "admin", "first-password", default_site.id
    )
    second_credential = db.add_credential(
        "Main", "admin", "second-password", second_site.id
    )
    assert db.add_switches(["10.0.0.201"], site_id=default_site.id) == 1
    assert db.add_switches(["10.0.0.201"], site_id=second_site.id) == 1

    assert [item.id for item in db.list_credentials(default_site.id)] == [
        first_credential.id
    ]
    assert [item.id for item in db.list_credentials(second_site.id)] == [
        second_credential.id
    ]
    assert [item.ip for item in db.list_switches(default_site.id)] == ["10.0.0.201"]
    assert [item.ip for item in db.list_switches(second_site.id)] == ["10.0.0.201"]

    db.delete_site(second_site.id)

    assert [site.id for site in db.list_sites()] == [default_site.id]
    assert db.list_credentials(second_site.id) == []
    assert db.list_switches(second_site.id) == []
    assert secrets.get(second_credential.id, "password") == ""


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
            INSERT INTO credentials(id, name, username)
            VALUES(7, 'Legacy Admin', 'admin');
            INSERT INTO switches(
                id, ip, name, last_credential_id, last_device_type
            ) VALUES(9, '10.0.0.201', 'Old Switch', 7, 'cisco_ios');
            """
        )

    db = Database(path, secrets=FakeSecrets())
    assert (tmp_path / "old.pre-sites.sqlite3").exists()
    site = db.list_sites()[0]
    credential = db.list_credentials(site.id)[0]
    switch = db.list_switches()[0]

    assert site.name == "Default Site"
    assert credential.id == 7
    assert credential.site_id == site.id
    assert switch.id == 9
    assert switch.site_id == site.id
    assert switch.last_credential_id == credential.id
    assert switch.model == ""
