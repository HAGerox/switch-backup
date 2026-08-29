from __future__ import annotations

import re

_INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(value: str) -> str:
    value = _INVALID.sub("_", value).strip().strip(".")
    value = re.sub(r"\s+", " ", value)
    return value or "Switch"


def backup_filename(ip: str, name: str) -> str:
    last_octet = ip.rsplit(".", 1)[-1]
    return f"{last_octet} - {safe_name(name)}.txt"
