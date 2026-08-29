import pytest

from switchbackup.ip_utils import parse_ip_range, parse_ip_spec, parse_single_ip


def test_single_ip():
    assert parse_ip_spec("10.1.2.3") == ["10.1.2.3"]


def test_short_range():
    assert parse_ip_spec("10.1.2.201-203") == [
        "10.1.2.201",
        "10.1.2.202",
        "10.1.2.203",
    ]


def test_full_range():
    assert parse_ip_spec("10.1.2.254-10.1.3.1") == [
        "10.1.2.254",
        "10.1.2.255",
        "10.1.3.0",
        "10.1.3.1",
    ]


def test_cidr_uses_hosts():
    assert parse_ip_spec("192.168.10.0/30") == ["192.168.10.1", "192.168.10.2"]


def test_reverse_range_rejected():
    with pytest.raises(ValueError):
        parse_ip_spec("10.1.2.220-201")


def test_single_ip_for_popup():
    assert parse_single_ip(" 10.1.2.3 ") == "10.1.2.3"


def test_separate_range_fields_for_popup():
    assert parse_ip_range("10.1.2.201", "10.1.2.203") == [
        "10.1.2.201",
        "10.1.2.202",
        "10.1.2.203",
    ]


def test_popup_rejects_invalid_ip_with_friendly_message():
    with pytest.raises(ValueError, match="valid IPv4 address"):
        parse_single_ip("not-an-ip")
