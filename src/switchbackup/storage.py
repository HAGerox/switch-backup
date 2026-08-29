from __future__ import annotations

import platform
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from .models import Credential, Switch

APP_NAME = "Switch Backup"
KEYRING_SERVICE = "Switch Backup"


def default_data_dir() -> Path:
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path.home() / ".switch-backup"


class KeychainStore:
    """Thin wrapper around keyring; on macOS this uses the user's Keychain."""

    def _key(self, credential_id: int, kind: str) -> str:
        return f"credential:{credential_id}:{kind}"

    def set(self, credential_id: int, kind: str, value: str) -> None:
        import keyring

        keyring.set_password(KEYRING_SERVICE, self._key(credential_id, kind), value)

    def get(self, credential_id: int, kind: str) -> str:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, self._key(credential_id, kind)) or ""

    def delete(self, credential_id: int, kind: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(KEYRING_SERVICE, self._key(credential_id, kind))
        except PasswordDeleteError:
            pass


class Database:
    def __init__(self, path: Path | None = None, secrets: KeychainStore | None = None):
        self.path = path or (default_data_dir() / "switch-backup.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.secrets = secrets or KeychainStore()
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS credentials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    username TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS switches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ip TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL DEFAULT '',
                    last_credential_id INTEGER NULL,
                    last_device_type TEXT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(last_credential_id) REFERENCES credentials(id) ON DELETE SET NULL
                );
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(switches)").fetchall()
            }
            if "model" not in columns:
                conn.execute(
                    "ALTER TABLE switches ADD COLUMN model TEXT NOT NULL DEFAULT ''"
                )

    # Credentials ---------------------------------------------------------
    def list_credentials(self) -> list[Credential]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, username FROM credentials ORDER BY id"
            ).fetchall()
        return [Credential(int(r["id"]), r["name"], r["username"]) for r in rows]

    def add_credential(
        self, name: str, username: str, password: str
    ) -> Credential:
        name = name.strip() or username.strip()
        username = username.strip()
        if not username:
            raise ValueError("Username is required.")
        if not password:
            raise ValueError("Password is required for a new credential.")

        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO credentials(name, username) VALUES(?, ?)",
                (name, username),
            )
            credential_id = int(cur.lastrowid)

        try:
            self.secrets.set(credential_id, "password", password)
        except Exception:
            with self._connect() as conn:
                conn.execute("DELETE FROM credentials WHERE id = ?", (credential_id,))
            raise

        return Credential(credential_id, name, username)

    def delete_credential(self, credential_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM credentials WHERE id = ?", (credential_id,))
        self.secrets.delete(credential_id, "password")
        # Clean up privileged-mode passwords saved by versions before 0.2.
        self.secrets.delete(credential_id, "secret")

    def get_credential_password(self, credential_id: int) -> str:
        return self.secrets.get(credential_id, "password")

    # Switches ------------------------------------------------------------
    def list_switches(self) -> list[Switch]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, ip, name, last_credential_id, last_device_type, model
                FROM switches
                ORDER BY
                    CAST(substr(ip, 1, instr(ip, '.') - 1) AS INTEGER),
                    ip
                """
            ).fetchall()
        # Numeric IPv4 sorting is done in Python; SQL order above is merely stable.
        items = [
            Switch(
                id=int(r["id"]),
                ip=r["ip"],
                name=r["name"],
                last_credential_id=(
                    int(r["last_credential_id"])
                    if r["last_credential_id"] is not None
                    else None
                ),
                last_device_type=r["last_device_type"],
                model=r["model"] or "",
            )
            for r in rows
        ]
        return sorted(items, key=lambda s: tuple(int(x) for x in s.ip.split(".")))

    def add_switches(self, ips: list[str], name: str = "") -> int:
        added = 0
        with self._connect() as conn:
            for ip in ips:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO switches(ip, name) VALUES(?, ?)",
                    (ip, name if len(ips) == 1 else ""),
                )
                added += int(cur.rowcount > 0)
        return added

    def delete_switches(self, ids: list[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            conn.execute(f"DELETE FROM switches WHERE id IN ({placeholders})", ids)

    def update_switch(self, switch_id: int, ip: str, name: str) -> bool:
        """Update a switch and clear discovery data when its IP changes."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ip FROM switches WHERE id = ?", (switch_id,)
            ).fetchone()
            if not row:
                return False
            ip_changed = row["ip"] != ip
            if ip_changed:
                conn.execute(
                    """
                    UPDATE switches
                    SET ip = ?, name = ?, last_credential_id = NULL,
                        last_device_type = NULL, model = ''
                    WHERE id = ?
                    """,
                    (ip, name.strip(), switch_id),
                )
            else:
                conn.execute(
                    "UPDATE switches SET name = ? WHERE id = ?",
                    (name.strip(), switch_id),
                )
        return ip_changed

    def update_switch_discovery(
        self,
        switch_id: int,
        credential_id: int,
        device_type: str,
        discovered_name: str,
        model: str = "",
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name, model FROM switches WHERE id = ?", (switch_id,)
            ).fetchone()
            if not row:
                return
            existing_name = row["name"] or ""
            name = existing_name or discovered_name
            detected_model = model or row["model"] or ""
            conn.execute(
                """
                UPDATE switches
                SET name = ?, last_credential_id = ?, last_device_type = ?, model = ?
                WHERE id = ?
                """,
                (name, credential_id, device_type, detected_model, switch_id),
            )
