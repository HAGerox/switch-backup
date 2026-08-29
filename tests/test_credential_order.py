from switchbackup.models import Credential, Switch
from switchbackup.network import CiscoBackupClient


def test_last_successful_credential_is_tried_first():
    creds = [
        Credential(1, "First", "a"),
        Credential(2, "Second", "b"),
        Credential(3, "Third", "c"),
    ]
    switch = Switch(1, "10.0.0.1", last_credential_id=2)
    ordered = CiscoBackupClient._ordered_credentials(switch, creds)
    assert [c.id for c in ordered] == [2, 1, 3]
