from pathlib import Path
from zipfile import ZipFile

from switchbackup.backup import BackupManager
from switchbackup.models import BackupResult, DiscoveryResult
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


class FakeClient:
    def __init__(self, credential_id):
        self.credential_id = credential_id

    def backup_one(self, switch, credentials):
        name = switch.name or f"SW-{switch.ip.rsplit('.', 1)[-1]}"
        return BackupResult(
            switch_id=switch.id,
            ip=switch.ip,
            ok=True,
            config=f"hostname {name}\ninterface Gi1/0/1\n",
            discovered_name=name,
            device_type="cisco_ios",
            credential_id=self.credential_id,
            message="Backed up",
        )

    def discover_one(self, switch, credentials):
        return DiscoveryResult(
            switch_id=switch.id,
            ip=switch.ip,
            ok=True,
            discovered_name=f"SW-{switch.ip.rsplit('.', 1)[-1]}",
            model="C9300-48P",
            device_type="cisco_ios",
            credential_id=self.credential_id,
            message="Discovered",
        )


def test_zip_contains_txt_backups_with_requested_names(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3", secrets=FakeSecrets())
    cred = db.add_credential("Main", "admin", "pw")
    db.add_switches(["10.0.0.201"], "Core Switch")
    db.add_switches(["10.0.0.202"])

    manager = BackupManager(db, max_workers=2)
    manager.client = FakeClient(cred.id)
    progress = []
    zip_path, results = manager.run(
        db.list_switches(),
        db.list_credentials(),
        downloads_dir=tmp_path,
        on_progress=lambda done, total, result: progress.append(
            (done, total, result.ip)
        ),
    )

    assert zip_path is not None
    assert all(r.ok for r in results)
    with ZipFile(zip_path) as archive:
        assert sorted(archive.namelist()) == [
            "201 - Core Switch.txt",
            "202 - SW-202.txt",
        ]
    assert sorted(done for done, _, _ in progress) == [1, 2]
    assert all(total == 2 for _, total, _ in progress)


def test_discovery_saves_model_and_reports_progress(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3", secrets=FakeSecrets())
    cred = db.add_credential("Main", "admin", "pw")
    db.add_switches(["10.0.0.201"])

    manager = BackupManager(db, max_workers=1)
    manager.client = FakeClient(cred.id)
    progress = []
    results = manager.discover(
        db.list_switches(),
        db.list_credentials(),
        on_progress=lambda done, total, result: progress.append((done, total)),
    )

    assert results[0].ok
    switch = db.list_switches()[0]
    assert switch.name == "SW-201"
    assert switch.model == "C9300-48P"
    assert progress == [(1, 1)]
