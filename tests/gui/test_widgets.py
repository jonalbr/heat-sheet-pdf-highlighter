from types import SimpleNamespace

from src.gui.widgets import TOOLTIP_DELAY_MS, TOOLTIP_OFFSET, Tooltip


class FakeWidget:
    def __init__(self):
        self.bindings = {}
        self.after_calls = []
        self.cancelled = []

    def bind(self, event_name, callback):
        self.bindings[event_name] = callback

    def after(self, delay_ms, callback):
        self.after_calls.append((delay_ms, callback))
        return f"after-{len(self.after_calls)}"

    def after_cancel(self, after_id):
        self.cancelled.append(after_id)

    def winfo_rootx(self):
        return 20

    def winfo_rooty(self):
        return 30

    def winfo_height(self):
        return 10


class FakeTooltipWindow:
    def __init__(self):
        self.geometry = None
        self.destroyed = False

    def wm_geometry(self, geometry):
        self.geometry = geometry

    def destroy(self):
        self.destroyed = True


def test_tooltip_schedules_after_hover_delay():
    widget = FakeWidget()
    tooltip = Tooltip(widget, "Helpful")

    widget.bindings["<Enter>"](SimpleNamespace(x_root=100, y_root=200))

    assert widget.after_calls == [(TOOLTIP_DELAY_MS, tooltip.show_tip)]
    assert tooltip._last_pointer == (100, 200)


def test_tooltip_follows_pointer_when_visible():
    widget = FakeWidget()
    tooltip = Tooltip(widget, "Helpful")
    tooltip.tooltip_window = FakeTooltipWindow()

    widget.bindings["<Motion>"](SimpleNamespace(x_root=100, y_root=200))

    assert tooltip.tooltip_window.geometry == f"+{100 + TOOLTIP_OFFSET[0]}+{200 + TOOLTIP_OFFSET[1]}"


def test_tooltip_leave_cancels_pending_show_and_closes_visible_window():
    widget = FakeWidget()
    tooltip = Tooltip(widget, "Helpful")
    window = FakeTooltipWindow()
    tooltip.tooltip_window = window

    widget.bindings["<Enter>"](SimpleNamespace(x_root=100, y_root=200))
    widget.bindings["<Leave>"]()

    assert widget.cancelled == ["after-1"]
    assert tooltip.tooltip_window is None
    assert window.destroyed


def test_tooltip_reuses_existing_binding_and_updates_text():
    widget = FakeWidget()
    Tooltip(widget, "Old")

    Tooltip(widget, "New", delay_ms=500)

    existing = widget._hsph_tooltip
    assert existing.text == "New"
    assert existing.delay_ms == 500
    assert set(widget.bindings) == {"<Enter>", "<Motion>", "<Leave>", "<ButtonPress>"}
