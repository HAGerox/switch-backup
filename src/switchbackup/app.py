from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import toga
from rubicon.objc import at
from toga.sources import AccessorColumn, ListSource
from toga.style import Pack
from toga.style.pack import CENTER, COLUMN, ROW
from toga_cocoa.libs import (
    NSBezelStyle,
    NSImage,
    NSImageScaleProportionallyDown,
)
from toga_cocoa.widgets.button import Button as CocoaButton

from .backup import BackupManager
from .ip_utils import parse_ip_range, parse_single_ip
from .macos_fields import MaskedTextInput
from .macos_table import NativeEditableTableImpl
from .network import prepare_networking
from .storage import Database


class SwitchTable(toga.Table):
    def __init__(self, *args, editable_accessors=(), on_cell_edit=None, **kwargs):
        self.editable_accessors = set(editable_accessors)
        self._on_cell_edit = on_cell_edit
        super().__init__(*args, **kwargs)

    def _create(self):
        return NativeEditableTableImpl(interface=self)

    def cell_edited(self, *, row, accessor, value):
        if self._on_cell_edit is not None:
            self._on_cell_edit(row=row, accessor=accessor, value=value)


class NativeListActionButtonImpl(CocoaButton):
    """Small AppKit list action button without changing the table widget."""

    def create(self):
        super().create()
        self.native.bordered = False
        self.native.image = NSImage.imageNamed(at(self.interface.image_name))
        self.native.imageScaling = NSImageScaleProportionallyDown
        self.native.toolTip = self.interface.help_text
        self.native.setAccessibilityLabel_(self.interface.help_text)

    def _set_button_style(self):
        self.native.bezelStyle = NSBezelStyle.ShadowlessSquare


class NativeListActionButton(toga.Button):
    def __init__(self, image_name: str, help_text: str, **kwargs):
        self.image_name = image_name
        self.help_text = help_text
        super().__init__("", **kwargs)

    def _create(self):
        return NativeListActionButtonImpl(interface=self)


class SwitchBackupApp(toga.App):
    def startup(self):
        prepare_networking()
        self.db = Database()
        self.backup_manager = BackupManager(self.db)
        self.status_by_ip: dict[str, str] = {}
        self.credential_popup: toga.Window | None = None
        self.switch_popup: toga.Window | None = None

        self.main_window = toga.MainWindow(
            title="Switch Backup",
            size=(720, 470),
            resizable=True,
        )

        tabs = toga.OptionContainer(
            content=[
                ("Credentials", self._build_credentials_tab()),
                ("Switches", self._build_switches_tab()),
            ]
        )
        self.main_window.content = tabs
        self._refresh_credentials()
        self._refresh_switches()
        self.main_window.show()

    async def on_running(self):
        await self._discover_undiscovered_switches()

    # ------------------------------------------------------------------
    # Credentials tab
    # ------------------------------------------------------------------
    def _build_credentials_tab(self):
        self.credential_table = toga.Table(
            columns=[
                AccessorColumn("Name", "name"),
                AccessorColumn("Username", "username"),
            ],
            multiple_select=False,
            on_select=self._credential_selection_changed,
            style=Pack(flex=1),
        )

        self.add_credential_button = self._list_action_button(
            "NSAddTemplate", "Add credential", self._show_credential_popup
        )
        self.remove_credential_button = self._list_action_button(
            "NSRemoveTemplate",
            "Remove selected credential",
            self._remove_credential,
            enabled=False,
        )
        actions = self._list_action_controls(
            self.add_credential_button,
            self.remove_credential_button,
        )

        return toga.Box(
            children=[
                self.credential_table,
                toga.Box(
                    children=[actions, toga.Box(style=Pack(flex=1))],
                    style=Pack(direction=ROW, align_items=CENTER, height=28),
                ),
            ],
            style=Pack(direction=COLUMN, margin=12, gap=0),
        )

    def _refresh_credentials(self):
        self.credential_table.data = [
            {"name": credential.name, "username": credential.username}
            for credential in self.db.list_credentials()
        ]
        self.remove_credential_button.enabled = False

    def _credential_selection_changed(self, widget, **kwargs):
        self.remove_credential_button.enabled = (
            self.credential_table.selection is not None
        )

    def _show_credential_popup(self, widget, **kwargs):
        if self.credential_popup and not self.credential_popup.closed:
            self.credential_popup.show()
            return

        self.new_credential_name = toga.TextInput(
            placeholder="For example: Main administrator"
        )
        self.new_credential_username = toga.TextInput(placeholder="Username")
        self.new_credential_password = MaskedTextInput(placeholder="Password")

        form = toga.Box(
            children=[
                self._field("Name (optional)", self.new_credential_name),
                self._field("Username", self.new_credential_username),
                self._field("Password", self.new_credential_password),
            ],
            style=Pack(direction=COLUMN, margin=16, gap=12, flex=1),
        )

        cancel_button = toga.Button("Cancel", on_press=self._close_credential_popup)
        save_button = toga.Button("Add Credential", on_press=self._save_credential)
        footer = toga.Box(
            children=[toga.Box(style=Pack(flex=1)), cancel_button, save_button],
            style=Pack(direction=ROW, margin=16, gap=8),
        )

        self.credential_popup = toga.Window(
            title="Add Credential",
            size=(460, 270),
            resizable=False,
            minimizable=False,
            content=toga.Box(
                children=[form, footer],
                style=Pack(direction=COLUMN),
            ),
        )
        self._disable_password_autofill(self.new_credential_username)
        self._disable_password_autofill(self.new_credential_password)
        self.credential_popup.show()

    async def _save_credential(self, widget, **kwargs):
        try:
            self.db.add_credential(
                self.new_credential_name.value,
                self.new_credential_username.value,
                self.new_credential_password.value,
            )
        except sqlite3.IntegrityError:
            await self._popup_error(
                self.credential_popup, "A credential with that name already exists."
            )
            return
        except Exception as exc:
            await self._popup_error(self.credential_popup, str(exc))
            return

        self._close_credential_popup()
        self._refresh_credentials()
        asyncio.create_task(self._discover_undiscovered_switches())

    async def _remove_credential(self, widget, **kwargs):
        row = self.credential_table.selection
        if row is None:
            return

        confirmed = await self.main_window.dialog(
            toga.ConfirmDialog(
                "Remove credential?",
                f'Remove "{row.name}" from Switch Backup?',
            )
        )
        if not confirmed:
            return

        credential = next(
            (item for item in self.db.list_credentials() if item.name == row.name),
            None,
        )
        if credential:
            self.db.delete_credential(credential.id)
        self._refresh_credentials()

    def _close_credential_popup(self, widget=None, **kwargs):
        if self.credential_popup and not self.credential_popup.closed:
            self.credential_popup.close()
        self.credential_popup = None

    # ------------------------------------------------------------------
    # Switches tab
    # ------------------------------------------------------------------
    def _build_switches_tab(self):
        self.switch_table = SwitchTable(
            columns=[
                AccessorColumn("IP Address", "ip"),
                AccessorColumn("Name", "name"),
                AccessorColumn("Model", "model"),
                AccessorColumn("Status", "status"),
            ],
            multiple_select=True,
            on_select=self._switch_selection_changed,
            editable_accessors={"ip", "name"},
            on_cell_edit=self._switch_cell_edited,
            style=Pack(flex=1),
        )

        self.add_switch_button = self._list_action_button(
            "NSAddTemplate", "Add switches", self._show_switch_popup
        )
        self.remove_switch_button = self._list_action_button(
            "NSRemoveTemplate",
            "Remove selected switches",
            self._remove_switches,
            enabled=False,
        )
        self.save_selected_button = toga.Button(
            "Save to Startup", on_press=self._save_selected, enabled=False
        )
        self.backup_selected_button = toga.Button(
            "Back Up Selected", on_press=self._backup_selected, enabled=False
        )

        self.progress_label = toga.Label(
            "",
            style=Pack(font_size=9, width=72),
        )
        self.backup_progress = toga.ProgressBar(
            max=1,
            value=0,
            style=Pack(width=135),
        )
        self.progress_box = toga.Box(
            children=[
                toga.Box(style=Pack(flex=1)),
                self.progress_label,
                self.backup_progress,
            ],
            style=Pack(direction=ROW, align_items=CENTER, gap=6),
        )

        self.switch_actions = toga.Box(
            children=[
                self._list_action_controls(
                    self.add_switch_button,
                    self.remove_switch_button,
                ),
                toga.Box(style=Pack(flex=1)),
                self.save_selected_button,
                self.backup_selected_button,
            ],
            style=Pack(direction=ROW, align_items=CENTER, gap=8, height=28),
        )
        self.switch_footer = toga.Box(
            children=[self.switch_actions],
            style=Pack(direction=COLUMN, gap=5),
        )

        return toga.Box(
            children=[self.switch_table, self.switch_footer],
            style=Pack(direction=COLUMN, margin=12, gap=0),
        )

    def _refresh_switches(self):
        rows = []
        for switch in self.db.list_switches():
            rows.append(
                {
                    "ip": switch.ip,
                    "name": switch.name or "—",
                    "model": switch.model or "—",
                    "status": self.status_by_ip.get(switch.ip, ""),
                    "switch_id": switch.id,
                }
            )
        self.switch_table.data = ListSource(
            accessors=["ip", "name", "model", "status", "switch_id"],
            data=rows,
        )
        self.remove_switch_button.enabled = False
        self.save_selected_button.enabled = False
        self.backup_selected_button.enabled = False

    def _switch_selection_changed(self, widget, **kwargs):
        has_selection = bool(self.switch_table.selection)
        self.remove_switch_button.enabled = has_selection
        self.save_selected_button.enabled = has_selection
        self.backup_selected_button.enabled = has_selection

    def _show_switch_popup(self, widget, **kwargs):
        if self.switch_popup and not self.switch_popup.closed:
            self.switch_popup.show()
            return

        self.single_ip_input = toga.TextInput(placeholder="192.168.1.10")
        self.single_name_input = toga.TextInput(placeholder="Optional display name")
        single_form = toga.Box(
            children=[
                self._field("IP address", self.single_ip_input),
                self._field("Name (optional)", self.single_name_input),
            ],
            style=Pack(direction=COLUMN, margin=16, gap=12),
        )

        self.range_start_input = toga.TextInput(placeholder="192.168.1.10")
        self.range_end_input = toga.TextInput(placeholder="192.168.1.30")
        range_form = toga.Box(
            children=[
                self._field("First IP address", self.range_start_input),
                self._field("Last IP address", self.range_end_input),
            ],
            style=Pack(direction=COLUMN, margin=16, gap=12),
        )

        self.switch_add_mode = toga.OptionContainer(
            content=[
                ("Single switch", single_form),
                ("IP range", range_form),
            ],
            style=Pack(flex=1),
        )

        cancel_button = toga.Button("Cancel", on_press=self._close_switch_popup)
        add_button = toga.Button("Add", on_press=self._save_switches)
        footer = toga.Box(
            children=[toga.Box(style=Pack(flex=1)), cancel_button, add_button],
            style=Pack(direction=ROW, margin=16, gap=8),
        )

        self.switch_popup = toga.Window(
            title="Add Switches",
            size=(500, 330),
            resizable=False,
            minimizable=False,
            content=toga.Box(
                children=[self.switch_add_mode, footer],
                style=Pack(direction=COLUMN),
            ),
        )
        self.switch_popup.show()

    async def _save_switches(self, widget, **kwargs):
        try:
            if self.switch_add_mode.current_tab.text == "Single switch":
                ips = [parse_single_ip(self.single_ip_input.value)]
                name = self.single_name_input.value.strip()
            else:
                ips = parse_ip_range(
                    self.range_start_input.value,
                    self.range_end_input.value,
                )
                name = ""

            existing_ips = {switch.ip for switch in self.db.list_switches()}
            added = self.db.add_switches(ips, name)
            if not added:
                message = (
                    "That switch is already in the list."
                    if len(ips) == 1
                    else "Every switch in that range is already in the list."
                )
                raise ValueError(message)
        except Exception as exc:
            await self._popup_error(self.switch_popup, str(exc))
            return

        self._close_switch_popup()
        self._refresh_switches()
        new_ips = [ip for ip in ips if ip not in existing_ips]
        await self._discover_switches(new_ips)

    def _switch_cell_edited(self, *, row, accessor, value):
        asyncio.get_running_loop().call_soon(
            lambda: asyncio.create_task(
                self._save_switch_cell(row.switch_id, accessor, value)
            )
        )

    async def _save_switch_cell(self, switch_id: int, accessor: str, value: str):
        switch = next(
            (item for item in self.db.list_switches() if item.id == switch_id),
            None,
        )
        if switch is None or accessor not in {"ip", "name"}:
            self._refresh_switches()
            return

        new_ip = switch.ip
        new_name = switch.name
        try:
            if accessor == "ip":
                new_ip = parse_single_ip(value)
            else:
                new_name = "" if value == "—" else value.strip()
            ip_changed = self.db.update_switch(
                switch_id,
                new_ip,
                new_name,
            )
        except sqlite3.IntegrityError:
            self._refresh_switches()
            await self._error("That IP address is already in the list.")
            return
        except Exception as exc:
            self._refresh_switches()
            await self._error(str(exc))
            return

        if ip_changed:
            self.status_by_ip.pop(switch.ip, None)
        self._refresh_switches()
        if ip_changed:
            await self._discover_switches([new_ip])

    async def _remove_switches(self, widget, **kwargs):
        switches = self._selected_switches()
        if not switches:
            return

        count = len(switches)
        confirmed = await self.main_window.dialog(
            toga.ConfirmDialog(
                "Remove switches?",
                f"Remove {count} selected switch{'es' if count != 1 else ''} from the list?",
            )
        )
        if not confirmed:
            return

        self.db.delete_switches([switch.id for switch in switches])
        for switch in switches:
            self.status_by_ip.pop(switch.ip, None)
        self._refresh_switches()

    def _close_switch_popup(self, widget=None, **kwargs):
        if self.switch_popup and not self.switch_popup.closed:
            self.switch_popup.close()
        self.switch_popup = None

    def _selected_switches(self):
        rows = self.switch_table.selection or []
        selected_ids = {row.switch_id for row in rows}
        return [
            switch
            for switch in self.db.list_switches()
            if switch.id in selected_ids
        ]

    async def _backup_selected(self, widget, **kwargs):
        switches = self._selected_switches()
        if not switches:
            return
        await self._run_backup(switches)

    async def _save_selected(self, widget, **kwargs):
        switches = self._selected_switches()
        if not switches:
            return

        count = len(switches)
        confirmed = await self.main_window.dialog(
            toga.ConfirmDialog(
                "Save running configurations?",
                "Replace the startup configuration on "
                f"{count} selected switch{'es' if count != 1 else ''} "
                "with the current running configuration?",
            )
        )
        if confirmed:
            await self._run_save(switches)

    async def _discover_switches(self, ips: list[str]):
        if not ips:
            return
        selected_ips = set(ips)
        switches = [
            switch for switch in self.db.list_switches() if switch.ip in selected_ips
        ]
        credentials = self.db.list_credentials()
        for switch in switches:
            self.status_by_ip[switch.ip] = "Discovering…"
        self._begin_progress("Discovering", len(switches))
        self._set_network_buttons_enabled(False)
        self._refresh_switches()

        loop = asyncio.get_running_loop()

        def on_progress(done, total, result):
            loop.call_soon_threadsafe(
                self._apply_discovery_progress, done, total, result
            )

        try:
            results = await asyncio.to_thread(
                self.backup_manager.discover,
                switches,
                credentials,
                on_progress,
            )
        except Exception as exc:
            self.backup_progress.stop()
            self._hide_progress()
            await self._error(str(exc))
            return
        finally:
            self._set_network_buttons_enabled(True)

        for result in results:
            self.status_by_ip[result.ip] = (
                "Discovered" if result.ok else f"Discovery failed: {result.message}"
            )
        self._finish_progress("Discovery complete", len(results), len(switches))
        self._refresh_switches()

    async def _discover_undiscovered_switches(self):
        if not self.db.list_credentials():
            return
        switches = [
            switch
            for switch in self.db.list_switches()
            if self._switch_needs_discovery(switch)
        ]
        if switches:
            await self._discover_switches([switch.ip for switch in switches])

    async def _run_backup(self, switches):
        credentials = self.db.list_credentials()
        if not credentials:
            await self._error("Add a credential before backing up switches.")
            return

        undiscovered = [
            switch
            for switch in switches
            if self._switch_needs_discovery(switch)
        ]
        if undiscovered:
            await self._discover_switches([switch.ip for switch in undiscovered])
            selected_ids = {switch.id for switch in switches}
            switches = [
                switch
                for switch in self.db.list_switches()
                if switch.id in selected_ids
            ]

        self.remove_switch_button.enabled = False
        self.save_selected_button.enabled = False
        self.backup_selected_button.enabled = False
        for switch in switches:
            self.status_by_ip[switch.ip] = "Backing up…"
        self._refresh_switches()

        self._begin_progress("Backing up", len(switches))
        self._set_network_buttons_enabled(False)
        loop = asyncio.get_running_loop()

        def on_progress(done, total, result):
            loop.call_soon_threadsafe(self._apply_backup_progress, done, total, result)

        try:
            zip_path, results = await asyncio.to_thread(
                self.backup_manager.run,
                switches,
                credentials,
                Path.home() / "Downloads",
                on_progress,
            )
        except Exception as exc:
            self.backup_progress.stop()
            self._hide_progress()
            await self._error(str(exc))
            return
        finally:
            self._set_network_buttons_enabled(True)

        for result in results:
            self.status_by_ip[result.ip] = (
                "Backed up" if result.ok else f"Failed: {result.message}"
            )
        self._refresh_switches()
        self._finish_progress("Backup complete", len(results), len(switches))

        failed = sum(1 for result in results if not result.ok)
        if zip_path:
            message = "Backup saved to Downloads."
            if failed:
                message += f" {failed} error{'s' if failed != 1 else ''}."
            await self.main_window.dialog(
                toga.InfoDialog(
                    "Backup complete",
                    message,
                )
            )
        else:
            await self._error("No switches were backed up successfully.")

    async def _run_save(self, switches):
        credentials = self.db.list_credentials()
        if not credentials:
            await self._error("Add a credential before saving switch configurations.")
            return

        undiscovered = [
            switch for switch in switches if self._switch_needs_discovery(switch)
        ]
        if undiscovered:
            await self._discover_switches([switch.ip for switch in undiscovered])
            selected_ids = {switch.id for switch in switches}
            switches = [
                switch
                for switch in self.db.list_switches()
                if switch.id in selected_ids
            ]

        for switch in switches:
            self.status_by_ip[switch.ip] = "Saving…"
        self._refresh_switches()
        self._begin_progress("Saving", len(switches))
        self._set_network_buttons_enabled(False)
        loop = asyncio.get_running_loop()

        def on_progress(done, total, result):
            loop.call_soon_threadsafe(self._apply_save_progress, done, total, result)

        try:
            results = await asyncio.to_thread(
                self.backup_manager.save_running,
                switches,
                credentials,
                on_progress,
            )
        except Exception as exc:
            self.backup_progress.stop()
            self._hide_progress()
            await self._error(str(exc))
            return
        finally:
            self._set_network_buttons_enabled(True)

        for result in results:
            self.status_by_ip[result.ip] = (
                "Saved to startup" if result.ok else f"Save failed: {result.message}"
            )
        self._refresh_switches()
        self._finish_progress("Save complete", len(results), len(switches))

        succeeded = sum(1 for result in results if result.ok)
        failed = len(results) - succeeded
        if succeeded:
            message = (
                f"Saved the running configuration to startup on {succeeded} "
                f"switch{'es' if succeeded != 1 else ''}."
            )
            if failed:
                message += f" {failed} error{'s' if failed != 1 else ''}."
            await self.main_window.dialog(toga.InfoDialog("Save complete", message))
        else:
            await self._error("No running configurations were saved successfully.")

    # ------------------------------------------------------------------
    # Shared UI helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _list_action_button(
        image_name: str,
        help_text: str,
        on_press,
        *,
        enabled: bool = True,
    ):
        return NativeListActionButton(
            image_name,
            help_text,
            on_press=on_press,
            enabled=enabled,
            style=Pack(width=28, height=24),
        )

    @staticmethod
    def _list_action_controls(add_button, remove_button):
        return toga.Box(
            children=[
                add_button,
                toga.Divider(
                    direction=toga.Divider.VERTICAL,
                    style=Pack(height=18),
                ),
                remove_button,
            ],
            style=Pack(direction=ROW, align_items=CENTER, gap=0),
        )

    def _field(self, label: str, widget):
        label_widget = toga.Label(label, style=Pack(width=120))
        widget.style.flex = 1
        return toga.Box(
            children=[label_widget, widget],
            style=Pack(direction=ROW, gap=10),
        )

    def _disable_password_autofill(self, widget):
        """Keep secure entry masked but opt out of macOS password suggestions."""
        try:
            native = widget._impl.native
            native.contentType = None
            native.automaticTextCompletionEnabled = False
            native.allowsWritingTools = False
        except Exception:
            # These are macOS-specific native properties.
            pass

    def _set_network_buttons_enabled(self, enabled: bool):
        self.add_switch_button.enabled = enabled
        if not enabled:
            self.remove_switch_button.enabled = False
            self.save_selected_button.enabled = False
            self.backup_selected_button.enabled = False

    @staticmethod
    def _switch_needs_discovery(switch) -> bool:
        if not switch.last_device_type or not switch.model:
            return True
        model = switch.model.lower()
        return (
            any(marker in model for marker in ("c1300", "c1200", "cbs", "s300"))
            and switch.last_device_type != "cisco_s300"
        )

    def _begin_progress(self, action: str, total: int):
        if self.progress_box.parent is None:
            self.switch_footer.add(self.progress_box)
        self.backup_progress.stop()
        self.backup_progress.max = max(1, total)
        self.backup_progress.value = 0
        self.backup_progress.start()
        self.progress_label.text = action

    def _finish_progress(self, action: str, done: int, total: int):
        self.backup_progress.value = done
        self.backup_progress.stop()
        self.progress_label.text = action
        self._hide_progress()

    def _hide_progress(self):
        self.progress_label.text = ""
        if self.progress_box.parent is self.switch_footer:
            self.switch_footer.remove(self.progress_box)

    def _apply_discovery_progress(self, done, total, result):
        self.backup_progress.value = done
        self.progress_label.text = "Discovering"
        self.status_by_ip[result.ip] = (
            "Discovered" if result.ok else f"Discovery failed: {result.message}"
        )
        self._refresh_switches()

    def _apply_backup_progress(self, done, total, result):
        self.backup_progress.value = done
        self.progress_label.text = "Backing up"
        self.status_by_ip[result.ip] = (
            "Backed up" if result.ok else f"Failed: {result.message}"
        )
        self._refresh_switches()

    def _apply_save_progress(self, done, total, result):
        self.backup_progress.value = done
        self.progress_label.text = "Saving"
        self.status_by_ip[result.ip] = (
            "Saved to startup" if result.ok else f"Save failed: {result.message}"
        )
        self._refresh_switches()

    async def _popup_error(self, popup: toga.Window | None, message: str):
        window = popup if popup and not popup.closed else self.main_window
        await window.dialog(toga.ErrorDialog("Switch Backup", message))

    async def _error(self, message: str):
        await self.main_window.dialog(toga.ErrorDialog("Switch Backup", message))


def main():
    return SwitchBackupApp("Switch Backup", "com.switchbackup.switchbackup")
