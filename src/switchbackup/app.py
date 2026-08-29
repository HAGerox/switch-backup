from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import toga
from rubicon.objc import at
from toga.dialogs import Dialog
from toga.sources import AccessorColumn, ListSource
from toga.style import Pack
from toga.style.pack import CENTER, COLUMN, ROW
from toga_cocoa.dialogs import NSAlertDialog
from toga_cocoa.libs import (
    NSAlertStyle,
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


class NativeActionDialogImpl(NSAlertDialog):
    """Native NSAlert with an action-specific primary button."""

    def __init__(self, title: str, message: str, action_title: str):
        self.action_title = action_title
        super().__init__(
            title=title,
            message=message,
            alert_style=NSAlertStyle.Informational,
            completion_handler=self.bool_completion_handler,
        )

    def build_dialog(self):
        action_button = self.native.addButtonWithTitle(self.action_title)
        cancel_button = self.native.addButtonWithTitle("Cancel")
        action_button.keyEquivalent = "\r"
        cancel_button.keyEquivalent = "\x1b"


class NativeActionDialog(Dialog[bool]):
    def __init__(self, title: str, message: str, action_title: str):
        self._impl = NativeActionDialogImpl(title, message, action_title)


class SwitchBackupApp(toga.App):
    def startup(self):
        prepare_networking()
        self.db = Database()
        self.backup_manager = BackupManager(self.db)
        self.sites = self.db.list_sites()
        self.active_site_id = self.sites[0].id
        self.status_by_switch_id: dict[int, str] = {}
        self._refreshing_sites = False
        self.site_popup: toga.Window | None = None
        self.credential_popup: toga.Window | None = None
        self.switch_popup: toga.Window | None = None

        self.main_window = toga.MainWindow(
            title="Switch Backup",
            size=(720, 470),
            resizable=True,
        )

        self.tabs = toga.OptionContainer(
            content=[
                ("Credentials", self._build_credentials_tab()),
                ("Switches", self._build_switches_tab()),
            ],
            style=Pack(flex=1),
        )
        self.main_window.content = toga.Box(
            children=[
                self._build_site_bar(),
                toga.Divider(),
                self.tabs,
            ],
            style=Pack(direction=COLUMN),
        )
        self._refresh_sites(self.active_site_id)
        self._refresh_credentials()
        self._refresh_switches()
        self.main_window.show()

    async def on_running(self):
        await self._discover_undiscovered_switches()

    # ------------------------------------------------------------------
    # Site context
    # ------------------------------------------------------------------
    def _build_site_bar(self):
        self.site_selector = toga.Selection(
            items=[site.name for site in self.sites],
            on_change=self._site_changed,
            style=Pack(width=250),
        )
        self.add_site_button = self._list_action_button(
            "NSAddTemplate", "Add site", self._show_site_popup
        )
        self.remove_site_button = self._list_action_button(
            "NSRemoveTemplate",
            "Remove current site",
            self._remove_site,
            enabled=len(self.sites) > 1,
        )
        return toga.Box(
            children=[
                toga.Label("Site", style=Pack(width=38)),
                self.site_selector,
                self._list_action_controls(
                    self.add_site_button,
                    self.remove_site_button,
                ),
                toga.Box(style=Pack(flex=1)),
            ],
            style=Pack(
                direction=ROW,
                align_items=CENTER,
                margin=(10, 12),
                gap=8,
            ),
        )

    def _refresh_sites(self, selected_site_id: int | None = None):
        self.sites = self.db.list_sites()
        selected_site_id = selected_site_id or self.active_site_id
        selected = next(
            (site for site in self.sites if site.id == selected_site_id),
            self.sites[0],
        )
        self.active_site_id = selected.id

        self._refreshing_sites = True
        try:
            self.site_selector.items = [site.name for site in self.sites]
            self.site_selector.value = selected.name
        finally:
            self._refreshing_sites = False
        self.remove_site_button.enabled = len(self.sites) > 1

    def _site_changed(self, widget, **kwargs):
        if self._refreshing_sites or self.site_selector.value is None:
            return
        site = next(
            (site for site in self.sites if site.name == self.site_selector.value),
            None,
        )
        if site is None or site.id == self.active_site_id:
            return
        self.active_site_id = site.id
        self.status_by_switch_id.clear()
        self._refresh_credentials()
        self._refresh_switches()

    def _show_site_popup(self, widget, **kwargs):
        if self.site_popup and not self.site_popup.closed:
            return

        self.new_site_name = toga.TextInput(
            placeholder="For example: London Office",
            on_change=self._site_form_changed,
        )
        self.site_error_label = toga.Label(
            "",
            style=Pack(color="#c42b1c", height=18),
        )
        cancel_button = toga.Button("Cancel", on_press=self._close_site_popup)
        self.add_site_confirm_button = toga.Button(
            "Add Site",
            on_press=self._save_site,
            enabled=False,
        )
        form = toga.Box(
            children=[
                self._field("Site name", self.new_site_name),
                self.site_error_label,
            ],
            style=Pack(direction=COLUMN, margin=20, gap=10, flex=1),
        )
        footer = toga.Box(
            children=[
                toga.Box(style=Pack(flex=1)),
                cancel_button,
                self.add_site_confirm_button,
            ],
            style=Pack(direction=ROW, margin=20, gap=8),
        )
        self.site_popup = toga.Window(
            title="Add Site",
            size=(460, 165),
            resizable=False,
            closable=False,
            minimizable=False,
            content=toga.Box(
                children=[form, footer],
                style=Pack(direction=COLUMN),
            ),
        )
        self._show_sheet(
            self.site_popup,
            first_responder=self.new_site_name,
            default_button=self.add_site_confirm_button,
            cancel_button=cancel_button,
        )

    def _site_form_changed(self, widget, **kwargs):
        self.site_error_label.text = ""
        self.add_site_confirm_button.enabled = bool(self.new_site_name.value.strip())

    async def _save_site(self, widget, **kwargs):
        try:
            site = self.db.add_site(self.new_site_name.value)
        except sqlite3.IntegrityError:
            self.site_error_label.text = "A site with that name already exists."
            return
        except Exception as exc:
            self.site_error_label.text = str(exc)
            return

        self._close_site_popup()
        self.status_by_switch_id.clear()
        self._refresh_sites(site.id)
        self._refresh_credentials()
        self._refresh_switches()

    async def _remove_site(self, widget, **kwargs):
        if len(self.sites) <= 1:
            return
        site = next(
            (site for site in self.sites if site.id == self.active_site_id),
            None,
        )
        if site is None:
            return
        credential_count, switch_count = self.db.site_counts(site.id)
        confirmed = await self.main_window.dialog(
            NativeActionDialog(
                f'Remove "{site.name}"?',
                "This will remove "
                f"{credential_count} credential{'s' if credential_count != 1 else ''} "
                f"and {switch_count} switch{'es' if switch_count != 1 else ''} "
                "from Switch Backup.",
                "Remove",
            )
        )
        if not confirmed:
            return

        self.db.delete_site(site.id)
        self.status_by_switch_id.clear()
        self._refresh_sites()
        self._refresh_credentials()
        self._refresh_switches()

    def _close_site_popup(self, widget=None, **kwargs):
        if self.site_popup and not self.site_popup.closed:
            self._close_sheet(self.site_popup)
        self.site_popup = None

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
        self.credential_empty_state = self._empty_state(
            "Start by adding a credential",
            "Switch Backup needs login details before it can discover or back up "
            "switches. Passwords are stored in macOS Keychain.",
            "Add Credential",
            self._show_credential_popup,
            "1. Add a credential   2. Add switches   3. Select switches to back up",
        )
        self.credential_content = toga.Box(style=Pack(flex=1))

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
        self.credential_list = toga.Box(
            children=[
                self.credential_table,
                toga.Box(
                    children=[actions, toga.Box(style=Pack(flex=1))],
                    style=Pack(direction=ROW, align_items=CENTER, height=28),
                ),
            ],
            style=Pack(direction=COLUMN, gap=0, flex=1),
        )

        return toga.Box(
            children=[self.credential_content],
            style=Pack(direction=COLUMN, margin=12, gap=0),
        )

    def _refresh_credentials(self):
        credentials = self.db.list_credentials(self.active_site_id)
        self.credential_table.data = [
            {"name": credential.name, "username": credential.username}
            for credential in credentials
        ]
        self._show_content(
            self.credential_content,
            self.credential_list if credentials else self.credential_empty_state,
        )
        self.remove_credential_button.enabled = False

    def _credential_selection_changed(self, widget, **kwargs):
        self.remove_credential_button.enabled = (
            self.credential_table.selection is not None
        )

    def _show_credential_popup(self, widget, **kwargs):
        if self.credential_popup and not self.credential_popup.closed:
            return

        self.new_credential_name = toga.TextInput(
            placeholder="For example: Main administrator",
            on_change=self._credential_form_changed,
        )
        self.new_credential_username = toga.TextInput(
            placeholder="Username",
            on_change=self._credential_form_changed,
        )
        self.new_credential_password = MaskedTextInput(
            placeholder="Password",
            on_change=self._credential_form_changed,
        )
        self.credential_error_label = toga.Label(
            "",
            style=Pack(color="#c42b1c", height=18),
        )

        form = toga.Box(
            children=[
                self._field("Name (optional)", self.new_credential_name),
                self._field("Username", self.new_credential_username),
                self._field("Password", self.new_credential_password),
                self.credential_error_label,
            ],
            style=Pack(direction=COLUMN, margin=20, gap=10, flex=1),
        )

        cancel_button = toga.Button("Cancel", on_press=self._close_credential_popup)
        self.save_credential_button = toga.Button(
            "Add Credential",
            on_press=self._save_credential,
            enabled=False,
        )
        footer = toga.Box(
            children=[
                toga.Box(style=Pack(flex=1)),
                cancel_button,
                self.save_credential_button,
            ],
            style=Pack(direction=ROW, margin=20, gap=8),
        )

        self.credential_popup = toga.Window(
            title="Add Credential",
            size=(480, 235),
            resizable=False,
            closable=False,
            minimizable=False,
            content=toga.Box(
                children=[form, footer],
                style=Pack(direction=COLUMN),
            ),
        )
        self._disable_password_autofill(self.new_credential_username)
        self._disable_password_autofill(self.new_credential_password)
        self._show_sheet(
            self.credential_popup,
            first_responder=self.new_credential_name,
            default_button=self.save_credential_button,
            cancel_button=cancel_button,
        )

    def _credential_form_changed(self, widget, **kwargs):
        self.credential_error_label.text = ""
        self.save_credential_button.enabled = bool(
            self.new_credential_username.value.strip()
            and self.new_credential_password.value
        )

    async def _save_credential(self, widget, **kwargs):
        try:
            self.db.add_credential(
                self.new_credential_name.value,
                self.new_credential_username.value,
                self.new_credential_password.value,
                self.active_site_id,
            )
        except sqlite3.IntegrityError:
            self.credential_error_label.text = (
                "A credential with that name already exists."
            )
            return
        except Exception as exc:
            self.credential_error_label.text = str(exc)
            return

        self._close_credential_popup()
        self._refresh_credentials()
        self._refresh_switches()
        asyncio.create_task(self._discover_undiscovered_switches())

    async def _remove_credential(self, widget, **kwargs):
        row = self.credential_table.selection
        if row is None:
            return

        confirmed = await self.main_window.dialog(
            NativeActionDialog(
                "Remove credential?",
                f'Remove "{row.name}" from Switch Backup?',
                "Remove",
            )
        )
        if not confirmed:
            return

        credential = next(
            (
                item
                for item in self.db.list_credentials(self.active_site_id)
                if item.name == row.name
            ),
            None,
        )
        if credential:
            self.db.delete_credential(credential.id)
        self._refresh_credentials()
        self._refresh_switches()

    def _close_credential_popup(self, widget=None, **kwargs):
        if self.credential_popup and not self.credential_popup.closed:
            self._close_sheet(self.credential_popup)
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
        self.switch_no_credentials_state = self._empty_state(
            "Add a credential first",
            "A credential is required before switches can be discovered, backed up, "
            "or saved.",
            "Go to Credentials",
            self._show_credentials_tab,
        )
        self.switch_empty_state = self._empty_state(
            "Add your first switch",
            "Add one IP address or an IP range. Reachable switches will be identified "
            "automatically.",
            "Add Switches",
            self._show_switch_popup,
        )
        self.switch_content = toga.Box(style=Pack(flex=1))

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
        self.switch_list = toga.Box(
            children=[self.switch_table, self.switch_footer],
            style=Pack(direction=COLUMN, gap=0, flex=1),
        )

        return toga.Box(
            children=[self.switch_content],
            style=Pack(direction=COLUMN, margin=12, gap=0),
        )

    def _refresh_switches(self):
        switches = self.db.list_switches(self.active_site_id)
        has_credentials = bool(self.db.list_credentials(self.active_site_id))
        rows = []
        for switch in switches:
            rows.append(
                {
                    "ip": switch.ip,
                    "name": switch.name or "—",
                    "model": switch.model or "—",
                    "status": self.status_by_switch_id.get(switch.id, ""),
                    "switch_id": switch.id,
                }
            )
        self.switch_table.data = ListSource(
            accessors=["ip", "name", "model", "status", "switch_id"],
            data=rows,
        )
        if switches:
            content = self.switch_list
        elif has_credentials:
            content = self.switch_empty_state
        else:
            content = self.switch_no_credentials_state
        self._show_content(self.switch_content, content)
        self.add_switch_button.enabled = has_credentials
        self.remove_switch_button.enabled = False
        self.save_selected_button.enabled = False
        self.backup_selected_button.enabled = False

    def _switch_selection_changed(self, widget, **kwargs):
        has_selection = bool(self.switch_table.selection)
        can_connect = has_selection and bool(
            self.db.list_credentials(self.active_site_id)
        )
        self.remove_switch_button.enabled = has_selection
        self.save_selected_button.enabled = can_connect
        self.backup_selected_button.enabled = can_connect

    def _show_switch_popup(self, widget, **kwargs):
        if self.switch_popup and not self.switch_popup.closed:
            return

        if not self.db.list_credentials(self.active_site_id):
            self._show_credentials_tab()
            return

        self.single_ip_input = toga.TextInput(
            placeholder="192.168.1.10",
            on_change=self._switch_form_changed,
        )
        self.single_name_input = toga.TextInput(placeholder="Optional display name")
        single_form = toga.Box(
            children=[
                self._field("IP address", self.single_ip_input),
                self._field("Name (optional)", self.single_name_input),
            ],
            style=Pack(direction=COLUMN, margin=16, gap=12),
        )

        self.range_start_input = toga.TextInput(
            placeholder="192.168.1.10",
            on_change=self._switch_form_changed,
        )
        self.range_end_input = toga.TextInput(
            placeholder="192.168.1.30",
            on_change=self._switch_form_changed,
        )
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
            on_select=self._switch_form_changed,
            style=Pack(flex=1),
        )

        cancel_button = toga.Button("Cancel", on_press=self._close_switch_popup)
        self.add_switch_confirm_button = toga.Button(
            "Add",
            on_press=self._save_switches,
            enabled=False,
        )
        self.switch_error_label = toga.Label(
            "",
            style=Pack(color="#c42b1c", margin_left=20, height=18),
        )
        footer = toga.Box(
            children=[
                toga.Box(style=Pack(flex=1)),
                cancel_button,
                self.add_switch_confirm_button,
            ],
            style=Pack(direction=ROW, margin=20, gap=8),
        )

        self.switch_popup = toga.Window(
            title="Add Switches",
            size=(540, 330),
            resizable=False,
            closable=False,
            minimizable=False,
            content=toga.Box(
                children=[self.switch_add_mode, self.switch_error_label, footer],
                style=Pack(direction=COLUMN),
            ),
        )
        self._show_sheet(
            self.switch_popup,
            first_responder=self.single_ip_input,
            default_button=self.add_switch_confirm_button,
            cancel_button=cancel_button,
        )

    def _switch_form_changed(self, widget, **kwargs):
        self.switch_error_label.text = ""
        current_tab = self.switch_add_mode.current_tab
        if current_tab is None:
            enabled = False
        elif current_tab.text == "Single switch":
            enabled = bool(self.single_ip_input.value.strip())
        else:
            enabled = bool(
                self.range_start_input.value.strip()
                and self.range_end_input.value.strip()
            )
        self.add_switch_confirm_button.enabled = enabled

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

            existing_ips = {
                switch.ip for switch in self.db.list_switches(self.active_site_id)
            }
            added = self.db.add_switches(ips, name, self.active_site_id)
            if not added:
                message = (
                    "That switch is already in the list."
                    if len(ips) == 1
                    else "Every switch in that range is already in the list."
                )
                raise ValueError(message)
        except Exception as exc:
            self.switch_error_label.text = str(exc)
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
            (
                item
                for item in self.db.list_switches(self.active_site_id)
                if item.id == switch_id
            ),
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
            self.status_by_switch_id.pop(switch.id, None)
        self._refresh_switches()
        if ip_changed:
            await self._discover_switches([new_ip])

    async def _remove_switches(self, widget, **kwargs):
        switches = self._selected_switches()
        if not switches:
            return

        count = len(switches)
        confirmed = await self.main_window.dialog(
            NativeActionDialog(
                "Remove switches?",
                f"Remove {count} selected switch{'es' if count != 1 else ''} from the list?",
                "Remove",
            )
        )
        if not confirmed:
            return

        self.db.delete_switches([switch.id for switch in switches])
        for switch in switches:
            self.status_by_switch_id.pop(switch.id, None)
        self._refresh_switches()

    def _close_switch_popup(self, widget=None, **kwargs):
        if self.switch_popup and not self.switch_popup.closed:
            self._close_sheet(self.switch_popup)
        self.switch_popup = None

    def _selected_switches(self):
        rows = self.switch_table.selection or []
        selected_ids = {row.switch_id for row in rows}
        return [
            switch
            for switch in self.db.list_switches(self.active_site_id)
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
            NativeActionDialog(
                "Save running configurations?",
                "Replace the startup configuration on "
                f"{count} selected switch{'es' if count != 1 else ''} "
                "with the current running configuration?",
                "Save",
            )
        )
        if confirmed:
            await self._run_save(switches)

    async def _discover_switches(self, ips: list[str]):
        if not ips:
            return
        selected_ips = set(ips)
        switches = [
            switch
            for switch in self.db.list_switches(self.active_site_id)
            if switch.ip in selected_ips
        ]
        credentials = self.db.list_credentials(self.active_site_id)
        for switch in switches:
            self.status_by_switch_id[switch.id] = "Discovering…"
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
            self.status_by_switch_id[result.switch_id] = (
                "Discovered" if result.ok else f"Discovery failed: {result.message}"
            )
        self._finish_progress("Discovery complete", len(results), len(switches))
        self._refresh_switches()

    async def _discover_undiscovered_switches(self):
        if not self.db.list_credentials(self.active_site_id):
            return
        switches = [
            switch
            for switch in self.db.list_switches(self.active_site_id)
            if self._switch_needs_discovery(switch)
        ]
        if switches:
            await self._discover_switches([switch.ip for switch in switches])

    async def _run_backup(self, switches):
        credentials = self.db.list_credentials(self.active_site_id)
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
                for switch in self.db.list_switches(self.active_site_id)
                if switch.id in selected_ids
            ]

        self.remove_switch_button.enabled = False
        self.save_selected_button.enabled = False
        self.backup_selected_button.enabled = False
        for switch in switches:
            self.status_by_switch_id[switch.id] = "Backing up…"
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
            self.status_by_switch_id[result.switch_id] = (
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
        credentials = self.db.list_credentials(self.active_site_id)
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
                for switch in self.db.list_switches(self.active_site_id)
                if switch.id in selected_ids
            ]

        for switch in switches:
            self.status_by_switch_id[switch.id] = "Saving…"
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
            self.status_by_switch_id[result.switch_id] = (
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
    def _empty_state(
        title: str,
        message: str,
        action_title: str,
        on_press,
        detail: str = "",
    ):
        children = [
            toga.Box(style=Pack(flex=1)),
            toga.Label(
                title,
                style=Pack(font_size=16, text_align=CENTER, width=500),
            ),
            toga.Label(
                message,
                style=Pack(text_align=CENTER, width=500),
            ),
        ]
        if detail:
            children.append(
                toga.Label(
                    detail,
                    style=Pack(font_size=10, text_align=CENTER, width=500),
                )
            )
        children.extend(
            [
                toga.Button(
                    action_title,
                    on_press=on_press,
                    style=Pack(width=160),
                ),
                toga.Box(style=Pack(flex=1)),
            ]
        )
        return toga.Box(
            children=children,
            style=Pack(direction=COLUMN, align_items=CENTER, gap=10, flex=1),
        )

    @staticmethod
    def _show_content(container: toga.Box, content):
        if container.children == [content]:
            return
        container.clear()
        container.add(content)

    def _show_credentials_tab(self, widget=None, **kwargs):
        self.tabs.current_tab = "Credentials"

    def _show_sheet(
        self,
        popup: toga.Window,
        *,
        first_responder,
        default_button: toga.Button,
        cancel_button: toga.Button,
    ):
        sheet = popup._impl.native
        default_native = default_button._impl.native
        cancel_native = cancel_button._impl.native
        default_native.keyEquivalent = "\r"
        cancel_native.keyEquivalent = "\x1b"
        sheet.setDefaultButtonCell_(default_native.cell)
        self.main_window._impl.native.beginSheet_completionHandler_(sheet, None)
        sheet.makeFirstResponder_(first_responder._impl.native)

    @staticmethod
    def _close_sheet(popup: toga.Window):
        sheet = popup._impl.native
        parent = sheet.sheetParent
        if parent is not None:
            parent.endSheet_(sheet)
        popup.close()

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
        self.site_selector.enabled = enabled
        self.add_site_button.enabled = enabled
        self.remove_site_button.enabled = enabled and len(self.sites) > 1
        self.add_switch_button.enabled = enabled and bool(
            self.db.list_credentials(self.active_site_id)
        )
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
        self.status_by_switch_id[result.switch_id] = (
            "Discovered" if result.ok else f"Discovery failed: {result.message}"
        )
        self._refresh_switches()

    def _apply_backup_progress(self, done, total, result):
        self.backup_progress.value = done
        self.progress_label.text = "Backing up"
        self.status_by_switch_id[result.switch_id] = (
            "Backed up" if result.ok else f"Failed: {result.message}"
        )
        self._refresh_switches()

    def _apply_save_progress(self, done, total, result):
        self.backup_progress.value = done
        self.progress_label.text = "Saving"
        self.status_by_switch_id[result.switch_id] = (
            "Saved to startup" if result.ok else f"Save failed: {result.message}"
        )
        self._refresh_switches()

    async def _error(self, message: str):
        await self.main_window.dialog(toga.ErrorDialog("Switch Backup", message))


def main():
    return SwitchBackupApp("Switch Backup", "com.switchbackup.switchbackup")
