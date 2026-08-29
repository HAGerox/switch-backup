from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable
from zipfile import ZIP_DEFLATED, ZipFile

from .filenames import backup_filename
from .models import BackupResult, Credential, DiscoveryResult, SaveResult, Switch
from .network import CiscoBackupClient
from .storage import Database


class BackupManager:
    def __init__(self, db: Database, max_workers: int = 5):
        self.db = db
        self.max_workers = max_workers
        self.client = CiscoBackupClient(db)

    def run(
        self,
        switches: list[Switch],
        credentials: list[Credential],
        downloads_dir: Path | None = None,
        on_progress: Callable[[int, int, BackupResult], None] | None = None,
    ) -> tuple[Path | None, list[BackupResult]]:
        if not switches:
            return None, []
        if not credentials:
            raise ValueError("Add at least one credential first.")

        workers = min(self.max_workers, len(switches))
        results: list[BackupResult] = []
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(self.client.backup_one, switch, credentials): switch
                for switch in switches
            }
            for future in as_completed(futures):
                switch = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = BackupResult(
                        switch_id=switch.id,
                        ip=switch.ip,
                        ok=False,
                        message=f"Unexpected error: {exc}",
                    )
                results.append(result)
                if on_progress:
                    on_progress(len(results), len(switches), result)

        results.sort(key=lambda r: tuple(int(x) for x in r.ip.split(".")))

        by_id = {s.id: s for s in switches}
        for result in results:
            if result.ok and result.credential_id is not None:
                self.db.update_switch_discovery(
                    result.switch_id,
                    result.credential_id,
                    result.device_type,
                    result.discovered_name,
                )

        successes = [r for r in results if r.ok]
        if not successes:
            return None, results

        folder = downloads_dir or (Path.home() / "Downloads")
        folder.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        zip_path = folder / f"Switch Backups - {timestamp}.zip"

        used_names: set[str] = set()
        with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
            for result in successes:
                switch = by_id[result.switch_id]
                chosen_name = switch.name.strip() or result.discovered_name or result.ip
                filename = backup_filename(result.ip, chosen_name)
                if filename in used_names:
                    stem = filename[:-4]
                    filename = f"{stem} ({result.ip}).txt"
                used_names.add(filename)
                archive.writestr(filename, result.config.encode("utf-8"))

        return zip_path, results

    def discover(
        self,
        switches: list[Switch],
        credentials: list[Credential],
        on_progress: Callable[[int, int, DiscoveryResult], None] | None = None,
    ) -> list[DiscoveryResult]:
        if not switches:
            return []

        workers = min(self.max_workers, len(switches))
        results: list[DiscoveryResult] = []
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(self.client.discover_one, switch, credentials): switch
                for switch in switches
            }
            for future in as_completed(futures):
                switch = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = DiscoveryResult(
                        switch_id=switch.id,
                        ip=switch.ip,
                        ok=False,
                        message=f"Unexpected error: {exc}",
                    )
                results.append(result)
                if result.ok and result.credential_id is not None:
                    self.db.update_switch_discovery(
                        result.switch_id,
                        result.credential_id,
                        result.device_type,
                        result.discovered_name,
                        result.model,
                    )
                if on_progress:
                    on_progress(len(results), len(switches), result)

        results.sort(key=lambda result: tuple(int(x) for x in result.ip.split(".")))
        return results

    def save_running(
        self,
        switches: list[Switch],
        credentials: list[Credential],
        on_progress: Callable[[int, int, SaveResult], None] | None = None,
    ) -> list[SaveResult]:
        if not switches:
            return []
        if not credentials:
            raise ValueError("Add at least one credential first.")

        workers = min(self.max_workers, len(switches))
        results: list[SaveResult] = []
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(self.client.save_one, switch, credentials): switch
                for switch in switches
            }
            for future in as_completed(futures):
                switch = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = SaveResult(
                        switch_id=switch.id,
                        ip=switch.ip,
                        ok=False,
                        message=f"Unexpected error: {exc}",
                    )
                results.append(result)
                if result.ok and result.credential_id is not None:
                    self.db.update_switch_discovery(
                        result.switch_id,
                        result.credential_id,
                        result.device_type,
                        result.discovered_name,
                    )
                if on_progress:
                    on_progress(len(results), len(switches), result)

        results.sort(key=lambda result: tuple(int(x) for x in result.ip.split(".")))
        return results
