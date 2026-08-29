from __future__ import annotations

import toga
from toga_cocoa.libs import SEL, NSRange, NSTextField, objc_method, objc_property
from toga_cocoa.widgets.textinput import TextInput as CocoaTextInput
from toga_cocoa.widgets.textinput import TogaTextFieldProxy

MASK_CHARACTER = "•"


class TogaMaskedTextField(NSTextField):
    """Native NSTextField that delegates masking to its Toga implementation."""

    interface = objc_property(object, weak=True)
    impl = objc_property(object, weak=True)

    @objc_method
    def textDidChange_(self, notification) -> None:
        self.impl.masked_text_did_change()

    @objc_method
    def becomeFirstResponder(self) -> bool:
        return TogaTextFieldProxy.becomeFirstResponder(__class__, self)

    @objc_method
    def textDidEndEditing_(self, textObject) -> None:
        TogaTextFieldProxy.textDidEndEditing_(__class__, self, textObject)

    @objc_method
    def control_textView_doCommandBySelector_(
        self,
        control,
        textView,
        selector: SEL,
    ) -> bool:
        selection = textView.selectedRange
        location = int(selection.location)
        length = int(selection.length)
        if selector.name == b"deleteBackward:":
            if length:
                self.impl.pending_change = (location, length, "")
            elif location:
                self.impl.pending_change = (location - 1, 1, "")
        elif selector.name == b"deleteForward:":
            self.impl.pending_change = (location, length or 1, "")
        return TogaTextFieldProxy.control_textView_doCommandBySelector_(
            __class__, self, control, textView, selector
        )


class MaskedTextInputImpl(CocoaTextInput):
    """Password-style input without macOS Password AutoFill suggestions.

    AppKit currently presents its Passwords control for every
    NSSecureTextField, even when contentType is nil. This implementation uses a
    standard native NSTextField and keeps only a bullet mask in the control;
    the actual value remains in the Python widget implementation.
    """

    def create(self):
        self._secret_value = ""
        self._visible_value = ""
        self._updating_mask = False
        self.pending_change = None
        super().create()

    def _make_instance(self):
        field = TogaMaskedTextField.new()
        field.contentType = None
        field.automaticTextCompletionEnabled = False
        return field

    def get_value(self):
        return self._secret_value

    def set_value(self, value):
        self._secret_value = "" if value is None else str(value)
        self._visible_value = MASK_CHARACTER * len(self._secret_value)
        self.native.stringValue = self._visible_value
        self.interface._value_changed()

    def masked_text_did_change(self):
        if self._updating_mask:
            return

        editor = self.native.currentEditor()
        observed = (
            str(editor.string) if editor is not None else str(self.native.stringValue)
        )
        previous = self._visible_value

        if self.pending_change is not None:
            prefix, removed_length, inserted = self.pending_change
            self.pending_change = None
            previous_end = prefix + removed_length
            self._secret_value = (
                self._secret_value[:prefix]
                + inserted
                + self._secret_value[previous_end:]
            )
        else:
            prefix = 0
            common_limit = min(len(previous), len(observed))
            while prefix < common_limit and previous[prefix] == observed[prefix]:
                prefix += 1

            suffix = 0
            while (
                suffix < len(previous) - prefix
                and suffix < len(observed) - prefix
                and previous[-(suffix + 1)] == observed[-(suffix + 1)]
            ):
                suffix += 1

            observed_end = len(observed) - suffix if suffix else len(observed)
            previous_end = len(previous) - suffix if suffix else len(previous)
            inserted = observed[prefix:observed_end]
            self._secret_value = (
                self._secret_value[:prefix]
                + inserted
                + self._secret_value[previous_end:]
            )
        self._visible_value = MASK_CHARACTER * len(self._secret_value)

        self._updating_mask = True
        try:
            if editor is not None:
                editor.string = self._visible_value
                editor.selectedRange = NSRange(prefix + len(inserted), 0)
            else:
                self.native.stringValue = self._visible_value
        finally:
            self._updating_mask = False

        self.interface.on_change()
        self.interface._validate()


class MaskedTextInput(toga.TextInput):
    def _create(self):
        return MaskedTextInputImpl(interface=self)
