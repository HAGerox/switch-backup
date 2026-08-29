from __future__ import annotations

import ipaddress
import re

MAX_RANGE_SIZE = 1024


def parse_single_ip(value: str) -> str:
    """Parse one IPv4 address with a user-friendly validation error."""
    text = value.strip()
    if not text:
        raise ValueError("Enter the switch IP address.")
    try:
        address = ipaddress.ip_address(text)
    except ValueError as exc:
        raise ValueError(
            "Enter a valid IPv4 address, such as 192.168.1.10."
        ) from exc
    if address.version != 4:
        raise ValueError("Only IPv4 addresses are supported.")
    return str(address)


def parse_ip_range(start_value: str, end_value: str) -> list[str]:
    """Parse an inclusive IPv4 range entered as separate first/last addresses."""
    if not start_value.strip() or not end_value.strip():
        raise ValueError("Enter both the first and last IP address.")
    start = parse_single_ip(start_value)
    end = parse_single_ip(end_value)
    return _enumerate_range(ipaddress.ip_address(start), ipaddress.ip_address(end))


def parse_ip_spec(spec: str) -> list[str]:
    """Parse a single IPv4 address, CIDR, or simple/full range.

    Accepted examples:
      10.0.0.201
      10.0.0.201-220
      10.0.0.201-10.0.0.220
      10.0.0.192/28
    """
    value = spec.strip()
    if not value:
        raise ValueError("Enter an IP address or range.")

    if "/" in value:
        network = ipaddress.ip_network(value, strict=False)
        if network.version != 4:
            raise ValueError("Only IPv4 addresses are supported.")
        addresses = [str(ip) for ip in network.hosts()]
        _check_size(addresses)
        return addresses

    short_range = re.fullmatch(r"(\d{1,3}(?:\.\d{1,3}){3})\s*-\s*(\d{1,3})", value)
    if short_range:
        start = ipaddress.ip_address(short_range.group(1))
        if start.version != 4:
            raise ValueError("Only IPv4 addresses are supported.")
        end_octet = int(short_range.group(2))
        if not 0 <= end_octet <= 255:
            raise ValueError("The final octet must be between 0 and 255.")
        start_octets = str(start).split(".")
        end = ipaddress.ip_address(".".join(start_octets[:3] + [str(end_octet)]))
        return _enumerate_range(start, end)

    full_range = re.fullmatch(r"([^\s]+)\s*-\s*([^\s]+)", value)
    if full_range:
        start = ipaddress.ip_address(full_range.group(1))
        end = ipaddress.ip_address(full_range.group(2))
        if start.version != 4 or end.version != 4:
            raise ValueError("Only IPv4 addresses are supported.")
        return _enumerate_range(start, end)

    ip = ipaddress.ip_address(value)
    if ip.version != 4:
        raise ValueError("Only IPv4 addresses are supported.")
    return [str(ip)]


def _enumerate_range(start: ipaddress.IPv4Address, end: ipaddress.IPv4Address) -> list[str]:
    if int(end) < int(start):
        raise ValueError("Range end must be greater than or equal to range start.")
    count = int(end) - int(start) + 1
    if count > MAX_RANGE_SIZE:
        raise ValueError(f"Range is too large ({count} addresses; maximum {MAX_RANGE_SIZE}).")
    return [str(ipaddress.ip_address(n)) for n in range(int(start), int(end) + 1)]


def _check_size(addresses: list[str]) -> None:
    if len(addresses) > MAX_RANGE_SIZE:
        raise ValueError(
            f"Range is too large ({len(addresses)} addresses; maximum {MAX_RANGE_SIZE})."
        )
