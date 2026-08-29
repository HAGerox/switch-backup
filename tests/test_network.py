import switchbackup.network as network_module
from switchbackup.models import Credential, Switch
from switchbackup.network import CiscoBackupClient


class FakeDatabase:
    def get_credential_password(self, credential_id):
        assert credential_id == 7
        return "password"


class FakeConnection:
    def __init__(self):
        self.base_prompt = "SW-201"
        self.commands = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def find_prompt(self):
        return "SW-201#"

    def send_command_timing(self, command, **kwargs):
        self.commands.append((command, kwargs))
        return "hostname SW-201\ninterface GigabitEthernet1/0/1\n"

    def send_command(self, command, **kwargs):
        self.commands.append((command, kwargs))
        return "hostname SW-201\ninterface GigabitEthernet1/0/1\n"


class FakeDiscoveryConnection(FakeConnection):
    def send_command_timing(self, command, **kwargs):
        self.commands.append((command, kwargs))
        if command == "show version":
            return "cisco C9300-48P (X86) processor with 8388608K bytes of memory"
        raise AssertionError(f"Unexpected command: {command}")


def test_backup_connection_only_fetches_running_config(monkeypatch):
    connection = FakeConnection()

    def connect_handler(**device):
        assert device["device_type"] == "cisco_ios"
        return connection

    monkeypatch.setattr(network_module, "_CONNECT_HANDLER", connect_handler)

    client = CiscoBackupClient(FakeDatabase())
    result = client._connect_and_fetch(
        Switch(3, "10.0.0.201"),
        Credential(7, "Main", "admin"),
        "cisco_ios",
    )

    assert result.ok
    assert result.config.startswith("hostname SW-201")
    assert connection.commands == [
        (
            "show running-config",
            {
                "read_timeout": 120,
                "strip_prompt": True,
                "strip_command": True,
            },
        )
    ]


def test_discovery_reads_show_version_and_identifies_model(monkeypatch):
    connection = FakeDiscoveryConnection()

    monkeypatch.setattr(
        network_module, "_CONNECT_HANDLER", lambda **device: connection
    )

    client = CiscoBackupClient(FakeDatabase())
    result = client._connect_and_discover(
        Switch(3, "10.0.0.201"),
        Credential(7, "Main", "admin"),
        "cisco_ios",
    )

    assert result.ok
    assert result.discovered_name == "SW-201"
    assert result.model == "C9300-48P"
    assert [command for command, _ in connection.commands] == ["show version"]


def test_model_parser_supports_inventory_pid():
    output = 'NAME: "1", DESCR: "Switch"\nPID: WS-C2960X-48FPS-L, VID: V02'
    assert CiscoBackupClient._model_from_output(output) == "WS-C2960X-48FPS-L"


def test_catalyst_1300_output_selects_small_business_driver():
    output = "Active-image: flash://system/images/image_c1300_4.1.9.85.bin"
    assert (
        CiscoBackupClient._device_type_from_output(output, "cisco_ios")
        == "cisco_s300"
    )


def test_hostname_removes_terminal_control_sequences():
    connection = FakeConnection()
    connection.base_prompt = "\x1b[KStageSwitch"
    assert CiscoBackupClient._hostname(connection, "\x1b[KStageSwitch#") == "StageSwitch"


def test_catalyst_1300_model_overrides_bad_cached_driver():
    switch = Switch(
        3,
        "10.0.0.201",
        last_device_type="cisco_nxos",
        model="C1300-24P-4X",
    )
    assert CiscoBackupClient._device_type_for_switch(switch) == "cisco_s300"


def test_paginated_running_config_is_rejected():
    config = "config-file-header\ninterface gi1\n--More-- or (q)uit"
    assert "incomplete" in CiscoBackupClient._config_problem(config)


def test_small_business_backup_requests_detailed_config(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(network_module, "_CONNECT_HANDLER", lambda **device: connection)

    client = CiscoBackupClient(FakeDatabase())
    result = client._connect_and_fetch(
        Switch(3, "10.0.0.201", model="C1300-24P-4X"),
        Credential(7, "Main", "admin"),
        "cisco_s300",
    )

    assert result.ok
    assert connection.commands[0][0] == "show running-config detailed"
