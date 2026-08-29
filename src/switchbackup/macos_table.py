from __future__ import annotations

from rubicon.objc import SEL, NSPoint, at, objc_method, objc_property

from toga_cocoa.libs import (
    NSBezelBorder,
    NSIndexSet,
    NSRange,
    NSScrollView,
    NSTableColumn,
    NSTableViewAnimation,
    NSTableViewColumnAutoresizingStyle,
)
from toga_cocoa.widgets.internal.cells import TogaIconView
from toga_cocoa.widgets.table import Table as CocoaTable
from toga_cocoa.widgets.table import TogaTable


class NativeEditableTable(TogaTable):
    """NSTableView that uses AppKit's normal inline cell editor."""

    editing_row = objc_property(int)
    editing_column = objc_property(int)

    @objc_method
    def tableView_viewForTableColumn_row_(self, table, column, row: int):
        data_row = self.interface.data[row]
        if (widget := column.toga_column.widget(data_row)) is not None:
            return widget._impl.native

        icon = column.toga_column.icon(data_row)
        text = column.toga_column.text(data_row, self.interface.missing_value)

        identifier = at(f"CellView_{self.interface.id}")
        cell_view = self.makeViewWithIdentifier(identifier, owner=self)
        if not cell_view:
            cell_view = TogaIconView.alloc().init()
            cell_view.identifier = identifier

        cell_view.setText(text)
        cell_view.setImage(icon._impl.native if icon else None)

        accessor = getattr(column.toga_column, "accessor", None)
        editable = accessor in self.interface.editable_accessors
        cell_view.textField.editable = editable
        cell_view.textField.selectable = editable
        cell_view.textField.delegate = self if editable else None
        return cell_view

    @objc_method
    def onDoubleClick_(self, sender) -> None:
        row = self.clickedRow
        column_index = self.clickedColumn
        if row < 0 or column_index < 0:
            return

        column = self.tableColumns[column_index]
        accessor = getattr(column.toga_column, "accessor", None)
        if accessor in self.interface.editable_accessors:
            self.editing_row = row
            self.editing_column = column_index
            self.editColumn(column_index, row=row, withEvent=None, select=True)
            return

        clicked = self.interface.data[row]
        self.interface.on_activate(row=clicked)

    @objc_method
    def controlTextDidEndEditing_(self, notification) -> None:
        row = self.editing_row
        column_index = self.editing_column
        self.editing_row = -1
        self.editing_column = -1
        if row < 0 or column_index < 0 or row >= len(self.interface.data):
            return

        column = self.tableColumns[column_index]
        accessor = getattr(column.toga_column, "accessor", None)
        self.interface.cell_edited(
            row=self.interface.data[row],
            accessor=accessor,
            value=str(notification.object.stringValue),
        )


class NativeEditableTableImpl(CocoaTable):
    def create(self):
        self.native = NSScrollView.alloc().init()
        self.native.hasVerticalScroller = True
        self.native.hasHorizontalScroller = False
        self.native.autohidesScrollers = False
        self.native.borderType = NSBezelBorder

        self.native_table = NativeEditableTable.alloc().init()
        self.native_table.interface = self.interface
        self.native_table.impl = self
        self.native_table.editing_row = -1
        self.native_table.editing_column = -1
        self.native_table.columnAutoresizingStyle = (
            NSTableViewColumnAutoresizingStyle.Uniform
        )
        self.native_table.usesAlternatingRowBackgroundColors = True
        self.native_table.allowsMultipleSelection = self.interface.multiple_select
        self.native_table.allowsColumnReordering = False

        self.columns = []
        if not self.interface._show_headings:
            self.native_table.setHeaderView(None)
        for index, toga_column in enumerate(self.interface._columns):
            self._insert_column(index, toga_column)

        self.native_table.delegate = self.native_table
        self.native_table.dataSource = self.native_table
        self.native_table.target = self.native_table
        self.native_table.doubleAction = SEL("onDoubleClick:")
        self.native.documentView = self.native_table
        self.add_constraints()
