from __future__ import annotations

import platform
import sqlite3
from contextlib import closing, contextmanager
from collections.abc import Iterator
from pathlib import Path

from .models import Credential, Site, Switch

APP_NAME = "Switch Backup"
KEYRING_SERVICE = "Switch Backup"
DEFAULT_SITE_NAME = "Default Site"


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
        self._backup_legacy_database()
        self._init_schema()

    def _backup_legacy_database(self) -> None:
        if not self.path.exists():
            return
        with closing(sqlite3.connect(self.path)) as source:
            columns = {
                row[1]
                for row in source.execute("PRAGMA table_info(credentials)").fetchall()
            }
            if not columns or "site_id" in columns:
                return
            backup_path = self.path.with_name(
                f"{self.path.stem}.pre-sites{self.path.suffix}"
            )
            if backup_path.exists():
                return
            with closing(sqlite3.connect(backup_path)) as destination:
                source.backup(destination)

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
            conn.execute("PRAGMA foreign_keys = OFF")
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if "credentials" not in tables or "switches" not in tables:
                self._create_site_schema(conn)
                self._ensure_default_site(conn)
                return

            credential_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(credentials)").fetchall()
            }
            switch_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(switches)").fetchall()
            }
            if "site_id" not in credential_columns or "site_id" not in switch_columns:
                self._migrate_to_sites(conn, switch_columns)
            else:
                self._create_site_schema(conn)
                self._ensure_default_site(conn)
                if "model" not in switch_columns:
                    conn.execute(
                        "ALTER TABLE switches ADD COLUMN model TEXT NOT NULL DEFAULT ''"
                    )

    @staticmethod
    def _create_site_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                username TEXT NOT NULL,
                FOREIGN KEY(site_id) REFERENCES sites(id) ON DELETE CASCADE,
                UNIQUE(site_id, name)
            );

            CREATE TABLE IF NOT EXISTS switches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id INTEGER NOT NULL,
                ip TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                last_credential_id INTEGER NULL,
                last_device_type TEXT NULL,
                model TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(site_id) REFERENCES sites(id) ON DELETE CASCADE,
                FOREIGN KEY(last_credential_id) REFERENCES credentials(id) ON DELETE SET NULL,
                UNIQUE(site_id, ip)
            );
            """
        )

    @staticmethod
    def _ensure_default_site(conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT id FROM sites ORDER BY id LIMIT 1").fetchone()
        if row:
            return int(row["id"])
        cursor = conn.execute(
            "INSERT INTO sites(name) VALUES(?)",
            (DEFAULT_SITE_NAME,),
        )
        return int(cursor.lastrowid)

    def _migrate_to_sites(
        self,
        conn: sqlite3.Connection,
        switch_columns: set[str],
    ) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS sites ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE)"
        )
        default_site_id = self._ensure_default_site(conn)

        conn.execute("ALTER TABLE switches RENAME TO switches_legacy")
        conn.execute("ALTER TABLE credentials RENAME TO credentials_legacy")
        self._create_site_schema(conn)

        conn.execute(
            """
            INSERT INTO credentials(id, site_id, name, username)
            SELECT id, ?, name, username FROM credentials_legacy
            """,
            (default_site_id,),
        )
        model_expression = "model" if "model" in switch_columns else "''"
        conn.execute(
            f"""
            INSERT INTO switches(
                id, site_id, ip, name, last_credential_id, last_device_type, model
            )
            SELECT id, ?, ip, name, last_credential_id, last_device_type,
                   {model_expression}
            FROM switches_legacy
            """,
            (default_site_id,),
        )
        conn.execute("DROP TABLE switches_legacy")
        conn.execute("DROP TABLE credentials_legacy")

    # Sites ---------------------------------------------------------------
    def list_sites(self) -> list[Site]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id, name FROM sites ORDER BY id").fetchall()
        return [Site(int(row["id"]), row["name"]) for row in rows]

    def add_site(self, name: str) -> Site:
        name = name.strip()
        if not name:
            raise ValueError("Site name is required.")
        with self._connect() as conn:
            cursor = conn.execute("INSERT INTO sites(name) VALUES(?)", (name,))
            return Site(int(cursor.lastrowid), name)

    def rename_site(self, site_id: int, name: str) -> Site:
        name = name.strip()
        if not name:
            raise ValueError("Site name is required.")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE sites SET name = ? WHERE id = ?",
                (name, site_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("That site no longer exists.")
        return Site(site_id, name)

    def delete_site(self, site_id: int) -> None:
        sites = self.list_sites()
        if len(sites) <= 1:
            raise ValueError("At least one site must remain.")

        credentials = self.list_credentials(site_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        for credential in credentials:
            self.secrets.delete(credential.id, "password")
            self.secrets.delete(credential.id, "secret")

    def site_counts(self, site_id: int) -> tuple[int, int]:
        with self._connect() as conn:
            credentials = conn.execute(
                "SELECT COUNT(*) FROM credentials WHERE site_id = ?", (site_id,)
            ).fetchone()[0]
            switches = conn.execute(
                "SELECT COUNT(*) FROM switches WHERE site_id = ?", (site_id,)
            ).fetchone()[0]
        return int(credentials), int(switches)

    def default_site_id(self) -> int:
        sites = self.list_sites()
        if not sites:
            raise RuntimeError("No sites are configured.")
        return sites[0].id

    # Credentials ---------------------------------------------------------
    def list_credentials(self, site_id: int | None = None) -> list[Credential]:
        parameters: tuple[int, ...] = ()
        where = ""
        if site_id is not None:
            where = "WHERE site_id = ?"
            parameters = (site_id,)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, name, username, site_id FROM credentials {where} ORDER BY id",
                parameters,
            ).fetchall()
        return [
            Credential(int(r["id"]), r["name"], r["username"], int(r["site_id"]))
            for r in rows
        ]

    def add_credential(
        self,
        name: str,
        username: str,
        password: str,
        site_id: int | None = None,
    ) -> Credential:
        name = name.strip() or username.strip()
        username = username.strip()
        if not username:
            raise ValueError("Username is required.")
        if not password:
            raise ValueError("Password is required for a new credential.")
        site_id = site_id or self.default_site_id()

        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO credentials(site_id, name, username) VALUES(?, ?, ?)",
                (site_id, name, username),
            )
            credential_id = int(cur.lastrowid)

        try:
            self.secrets.set(credential_id, "password", password)
        except Exception:
            with self._connect() as conn:
                conn.execute("DELETE FROM credentials WHERE id = ?", (credential_id,))
            raise

        return Credential(credential_id, name, username, site_id)

    def delete_credential(self, credential_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM credentials WHERE id = ?", (credential_id,))
        self.secrets.delete(credential_id, "password")
        # Clean up privileged-mode passwords saved by versions before 0.2.
        self.secrets.delete(credential_id, "secret")

    def get_credential_password(self, credential_id: int) -> str:
        return self.secrets.get(credential_id, "password")

    # Switches ------------------------------------------------------------
    def list_switches(self, site_id: int | None = None) -> list[Switch]:
        parameters: tuple[int, ...] = ()
        where = ""
        if site_id is not None:
            where = "WHERE site_id = ?"
            parameters = (site_id,)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, ip, name, last_credential_id, last_device_type, model,
                       site_id
                FROM switches
                {where}
                ORDER BY
                    CAST(substr(ip, 1, instr(ip, '.') - 1) AS INTEGER),
                    ip
                """,
                parameters,
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
                site_id=int(r["site_id"]),
            )
            for r in rows
        ]
        return sorted(items, key=lambda s: tuple(int(x) for x in s.ip.split(".")))

    def add_switches(
        self,
        ips: list[str],
        name: str = "",
        site_id: int | None = None,
    ) -> int:
        site_id = site_id or self.default_site_id()
        added = 0
        with self._connect() as conn:
            for ip in ips:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO switches(site_id, ip, name) VALUES(?, ?, ?)",
                    (site_id, ip, name if len(ips) == 1 else ""),
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
