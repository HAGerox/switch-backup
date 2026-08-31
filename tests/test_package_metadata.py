import tomllib
from pathlib import Path

from switchbackup import __version__


def load_briefcase_config():
    return tomllib.loads(Path("pyproject.toml").read_text())["tool"]["briefcase"]


def test_macos_bundle_declares_local_network_usage():
    description = load_briefcase_config()["app"]["switchbackup"]["macOS"]["info"][
        "NSLocalNetworkUsageDescription"
    ]

    assert "local network" in description.lower()
    assert "ssh" in description.lower()


def test_package_version_matches_briefcase_version():
    assert __version__ == load_briefcase_config()["version"]
