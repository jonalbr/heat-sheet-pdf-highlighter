"""Dev Tools lifetimes, guarded actions, placement and real Tk layouts."""

import datetime
import gc
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_settings_reset import app as app
from src.gui import dev_tools as module
from src.gui import dev_tools_geometry as geometry
from src.gui.dev_tools import _RefreshContext
from src.gui.dev_tools_geometry import Bounds, beside_parent, clamp_bounds, window_bounds
from src.gui.main_window import ProcessingJob


REAL_THREAD = threading.Thread
NOW = datetime.datetime.now()


@pytest.fixture(autouse=True)
def collect_tk_cycles_on_the_main_thread():
    """Do not leave destroyed Tk variables for a later PDF worker's cyclic GC."""
    gc.collect()
    yield
    gc.collect()


def release(tag="2.1.0", sha=True):
    return dict(
        tag=tag,
        exe_url=f"https://example.invalid/{tag}.exe",
        sha_url=f"https://example.invalid/{tag}.sha256" if sha else None,
        body=f"# Release {tag}\n\nSome improvements.\n\n- A change\n" * 5,
    )


@pytest.fixture
def dev(app, monkeypatch):
    monkeypatch.setattr(module, "load_releases_cache", lambda: (None, None, None))
    monkeypatch.setattr(module, "save_releases_cache", Mock())
    app.root.deiconify()
    app.root.update()
    app.dev_tools.open()
    app.root.update()
    return app.dev_tools


@pytest.fixture
def workers(monkeypatch):
    pending = []

    class Thread:
        def __init__(self, *, target, daemon):
            self.target = target

        def start(self):
            pending.append(self.target)

    monkeypatch.setattr(module.threading, "Thread", Thread)
    return pending


@pytest.mark.parametrize(
    "parent,size,work,expected",
    [
        (Bounds(100, 100, 600, 500), (400, 300), Bounds(0, 30, 1400, 900), Bounds(616, 150, 1016, 450)),
        (Bounds(800, 100, 1300, 500), (400, 300), Bounds(0, 30, 1400, 900), Bounds(384, 150, 784, 450)),
        (Bounds(300, 100, 1000, 500), (800, 300), Bounds(0, 30, 1400, 900), Bounds(600, 150, 1400, 450)),
        (Bounds(-1700, 100, -1200, 500), (400, 300), Bounds(-1920, 40, 0, 1040), Bounds(-1184, 150, -784, 450)),
        (Bounds(0, -100, 600, 300), (400, 600), Bounds(0, 40, 1400, 700), Bounds(616, 40, 1016, 640)),
        (Bounds(0, 600, 600, 1000), (400, 600), Bounds(0, 40, 1400, 700), Bounds(616, 100, 1016, 700)),
        (Bounds(100, 100, 600, 500), (1600, 1200), Bounds(0, 30, 1400, 900), Bounds(0, 30, 1400, 900)),
    ],
)
def test_placement_prefers_right_then_left_and_clamps_complete_frame(parent, size, work, expected):
    assert beside_parent(parent, size, work) == expected


def test_clamping_preserves_position_until_an_edge_requires_adjustment():
    work = Bounds(-1920, 20, 0, 1040)
    assert clamp_bounds(Bounds(-1800, 100, -1000, 800), work) == Bounds(-1800, 100, -1000, 800)
    assert clamp_bounds(Bounds(-1000, 800, 500, 1500), work) == Bounds(-1500, 340, 0, 1040)


def test_tk_bounds_fallback_when_native_apis_are_unavailable(monkeypatch):
    monkeypatch.setattr(geometry, "_user32", Mock(side_effect=OSError("not available")))
    parent = SimpleNamespace(
        winfo_rootx=lambda: 100,
        winfo_rooty=lambda: 200,
        winfo_width=lambda: 500,
        winfo_height=lambda: 400,
        winfo_vrootx=lambda: -800,
        winfo_vrooty=lambda: 20,
        winfo_screenwidth=lambda: 1800,
        winfo_screenheight=lambda: 900,
    )
    assert window_bounds(parent) == Bounds(100, 200, 600, 600)
    assert geometry.monitor_work_area(parent) == Bounds(-800, 20, 1000, 920)
    assert geometry.frame_padding(parent) == (0, 0)


@pytest.mark.parametrize("language", ["de", "en"])
def test_first_visible_frame_is_final_and_parent_never_moves(app, monkeypatch, language):
    app.on_language_change(language)
    app.root.geometry("760x488+100+150")
    app.root.deiconify()
    app.root.update()
    before = window_bounds(app.root)
    mapped = []
    real_toplevel = module.Toplevel

    def create(parent):
        window = real_toplevel(parent)
        window.bind("<Map>", lambda event: mapped.append(window_bounds(window)) if event.widget is window else None)
        return window

    monkeypatch.setattr(module, "Toplevel", create)
    app.dev_tools.open()
    dev = app.dev_tools
    app.root.update()
    assert mapped == [window_bounds(dev.window)]
    assert mapped[0] == beside_parent(
        before, (dev._auto_size[0] + dev._padding[0], dev._auto_size[1] + dev._padding[1]), dev._work
    )
    assert window_bounds(app.root) == before
    assert dev.window.resizable() == (1, 1)
    assert not dev._user_sized


@pytest.mark.parametrize("old_fails", [False, True])
def test_old_channel_success_and_error_cannot_replace_current_data_or_cache(dev, workers, monkeypatch, old_fails):
    def fetch(channel):
        if channel == "stable" and old_fails:
            raise RuntimeError("internal old-channel failure")
        return [release("2.2.0rc1" if channel == "rc" else "2.1.0")]

    monkeypatch.setattr(dev.app.update_checker, "list_releases", fetch)
    status = dev.app.status_var.get()
    dev._start_refresh_releases_async(force=True)
    old_context = dev._context
    dev.app.app_settings.update_setting("update_channel", "rc")
    dev._start_refresh_releases_async(force=True)
    assert dev.releases_combo.get() == ""
    workers.pop(1)()
    dev._drain_queue()
    assert dev.releases_combo.get() == "2.2.0rc1"
    workers.pop(0)()
    dev._complete_refresh(old_context, [release("cached-old")], False, None)
    dev._drain_queue()
    assert dev.releases_combo.get() == "2.2.0rc1"
    assert module.save_releases_cache.call_count == 1
    assert module.save_releases_cache.call_args.kwargs["channel"] == "rc"
    assert dev.release_hint.cget("text") == ""
    assert dev.app.status_var.get() == status


def test_worker_uses_captured_channel_and_does_not_read_tk(dev, workers, monkeypatch):
    channels = []

    def fetch(channel):
        assert threading.current_thread() is not threading.main_thread()
        channels.append(channel)
        return [release()]

    monkeypatch.setattr(dev.app.update_checker, "list_releases", fetch)
    dev._start_refresh_releases_async(force=True)
    monkeypatch.setattr(dev, "_get_channel", lambda: pytest.fail("Worker read frontend state"))
    thread = REAL_THREAD(target=workers.pop())
    thread.start()
    thread.join(2)
    assert not thread.is_alive()
    assert channels == ["stable"]
    assert dev._dispatch_queue.qsize() == 1


@pytest.mark.parametrize("close_way", ["close", "destroy", "shutdown"])
def test_closed_lifetimes_reject_queued_and_late_completions(dev, workers, monkeypatch, close_way):
    monkeypatch.setattr(dev.app.update_checker, "list_releases", lambda channel: [release()])
    dev._start_refresh_releases_async(force=True)
    old = dev._context
    timer = dev._dispatcher_id
    if close_way == "destroy":
        dev.window.destroy()
    elif close_way == "shutdown":
        dev.app._closing = True
        dev._complete_refresh(old, [release()], False, NOW)
        assert dev.releases_combo.get() == ""
        dev.app._closing = False
        dev.close()
    else:
        dev.close()
    assert dev.window is None
    assert timer not in dev.app.root.tk.call("after", "info")
    workers.pop()()
    assert dev._dispatch_queue.empty()
    dev.open()
    dev._complete_refresh(old, [release()], False, NOW)
    dev._complete_refresh(old, None, True, None)
    assert dev.releases_combo.get() == ""
    assert module.save_releases_cache.call_count == 0


def test_dispatcher_only_publishes_current_messages_and_stops_on_close(dev, workers, monkeypatch):
    monkeypatch.setattr(dev.app.update_checker, "list_releases", lambda channel: [release()])
    dev._start_refresh_releases_async(force=True)
    timer = dev._dispatcher_id
    workers.pop()()
    # Run the actual scheduled callback once, avoiding elapsed-time sleeps.
    callback = dev.app.root.tk.call("after", "info", timer)[0]
    dev.app.root.after_cancel(timer)
    dev._dispatcher_id = None
    dev._poll_dispatcher()
    assert callback
    assert dev.releases_combo.get() == "2.1.0"
    assert dev._dispatcher_id is not None
    dev.close()
    assert dev._dispatcher_id is None


@pytest.mark.parametrize("has_cache", [False, True])
@pytest.mark.parametrize("language", ["de", "en"])
def test_loading_failures_are_actionable_inline_and_keep_cache(dev, workers, monkeypatch, caplog, has_cache, language):
    dev.app.on_language_change(language)
    if has_cache:
        monkeypatch.setattr(module, "load_releases_cache", lambda: (NOW, "stable", [release()]))

    def fail(channel):
        raise RuntimeError("debug worker /private/path failed")

    monkeypatch.setattr(dev.app.update_checker, "list_releases", fail)
    dev._start_refresh_releases_async(force=True)
    workers.pop()()
    dev._drain_queue()
    text = dev.release_hint.cget("text")
    assert text == dev.app.strings["dev_versions_cached" if has_cache else "dev_versions_failed"]
    assert "debug worker" not in text
    assert "debug worker" in caplog.text
    assert bool(dev.releases_combo.get()) is has_cache
    assert str(dev.retry_button.cget("state")) == "normal"


def test_fresh_cache_avoids_fetch_until_retry_and_keeps_selection(dev, workers, monkeypatch):
    monkeypatch.setattr(module, "load_releases_cache", lambda: (NOW, "stable", [release(), release("2.0.0")]))
    dev._start_refresh_releases_async()
    assert workers == []
    dev.releases_combo.set("2.0.0")
    dev._selection_changed()
    dev._start_refresh_releases_async(force=True)
    assert len(workers) == 1
    assert dev.releases_combo.get() == "2.0.0"
    context = dev._context
    dev._complete_refresh(context, [], False, NOW)
    assert dev.releases_combo.get() == ""
    assert dev.release_hint.cget("text") == dev.app.strings["dev_versions_empty"]
    assert str(dev.install_button.cget("state")) == "disabled"


def test_cache_write_failure_does_not_hide_loaded_releases_or_publish_diagnostics(dev, monkeypatch, caplog):
    monkeypatch.setattr(module, "save_releases_cache", Mock(side_effect=OSError("internal cache failure")))
    dev._context = context = _RefreshContext(dev._generation, 1, "stable")
    dev._complete_refresh(context, [release()], False, NOW)
    assert dev.releases_combo.get() == "2.1.0"
    assert "internal cache failure" in caplog.text
    assert "internal cache failure" not in dev.release_hint.cget("text")


@pytest.mark.parametrize("state", ["processing", "cancelling", "download", "cancelled_download", "reserved"])
def test_busy_actions_and_direct_handlers_are_locked_while_browsing_stays_available(dev, monkeypatch, state):
    app = dev.app
    dev._apply_releases([release()])
    if state in ("processing", "cancelling"):
        app._job = ProcessingJob(1, "source.pdf", app._collect_processing_options())
        if state == "cancelling":
            app._job.cancel.set()
    elif state == "reserved":
        app.update_checker._active_download = True
    else:
        app.start_download()
        if state == "cancelled_download":
            app.abort_download()
    dev.set_busy(False)  # Handler guards also consult the actual app reservation.
    for control in (dev.channel_combo, dev.sha_checkbox, dev.install_button, dev.reset_button):
        assert str(control.cget("state")) == "disabled"
    assert str(dev.releases_combo.cget("state")) == "readonly"
    assert str(dev.notes_button.cget("state")) == "normal"
    monkeypatch.setattr(module, "ask_ok_cancel", lambda *args: pytest.fail("Busy action prompted"))
    dev.channel_var.set("rc")
    dev._on_channel_changed()
    dev.sha_required.set(False)
    dev._set_sha_required()
    dev._install_selected_release()
    dev._reset_settings()
    assert app.app_settings.snapshot()["update_channel"] == "stable"
    assert app.app_settings.snapshot()["verify_sha"] == "True"
    assert dev.channel_var.get() == "stable" and dev.sha_required.get()
    app._job = None
    app.download_active = False
    app.update_checker._active_download = False
    dev.set_busy(False)
    assert str(dev.channel_combo.cget("state")) == "readonly"
    assert str(dev.install_button.cget("state")) == "normal"


@pytest.mark.parametrize("verify", [False, True])
@pytest.mark.parametrize("sha", [False, True])
@pytest.mark.parametrize("exe", [False, True])
def test_install_eligibility_is_explicit_and_follows_checksum_policy(dev, verify, sha, exe):
    record = release(sha=sha)
    if not exe:
        record["exe_url"] = None
    dev.sha_required.set(verify)
    dev._apply_releases([record])
    assert (str(dev.install_button.cget("state")) == "normal") is (exe and (sha or not verify))
    if exe and verify and not sha:
        assert dev.release_hint.cget("text") == dev.app.strings["dev_checksum_missing"]


@pytest.mark.parametrize("change", ["busy", "close", "selection", "refresh", "checksum", "metadata", "channel"])
def test_install_revalidates_after_nested_confirmation(dev, workers, monkeypatch, change):
    dev._apply_releases([release(), release("2.0.0")])

    def confirm(*args):
        if change == "busy":
            dev.set_busy(True)
        elif change == "close":
            dev.close()
        elif change == "selection":
            dev.releases_combo.set("2.0.0")
        elif change == "refresh":
            dev._context = _RefreshContext(dev._generation, 100, "stable")
        elif change == "checksum":
            dev.sha_required.set(False)
        elif change == "channel":
            dev.app.app_settings.update_setting("update_channel", "rc")
        else:
            dev._releases_cache["2.1.0"]["exe_url"] += "?changed"
        return True

    monkeypatch.setattr(module, "ask_ok_cancel", confirm)
    dev._install_selected_release()
    assert workers == []


def test_fresh_empty_cache_clears_previous_versions_without_fetching(dev, workers, monkeypatch):
    dev._apply_releases([release()])
    monkeypatch.setattr(module, "load_releases_cache", lambda: (NOW, "stable", []))
    dev._start_refresh_releases_async()
    assert dev.releases_combo.get() == ""
    assert workers == []


def test_invalid_cache_does_not_prevent_network_retry(dev, workers, monkeypatch):
    monkeypatch.setattr(module, "load_releases_cache", lambda: (NOW, "stable", None))
    dev._start_refresh_releases_async()
    assert len(workers) == 1


def test_pending_failure_uses_current_language_without_rebuilding_window(dev, workers, monkeypatch):
    monkeypatch.setattr(dev.app.update_checker, "list_releases", Mock(side_effect=OSError("internal failure")))
    dev._start_refresh_releases_async(force=True)
    window = dev.window
    context = dev._context
    dev.app.on_language_change("en")
    assert dev.window is window and dev._context == context
    workers.pop()()
    dev._drain_queue()
    assert dev.release_hint.cget("text") == "Could not load versions. Please try again."


def test_html_wheel_and_scrollbar_events_do_not_also_scroll_outer_form(dev, monkeypatch):
    scroll = Mock()
    monkeypatch.setattr(dev.canvas, "yview_scroll", scroll)
    for widget in (dev.scrollbar, dev.channel_combo, dev.releases_combo, dev.notes_html.html):
        dev._scroll_form(SimpleNamespace(widget=widget, delta=-120))
    scroll.assert_not_called()
    dev._scroll_form(SimpleNamespace(widget=dev.release_hint, delta=-120))
    scroll.assert_called_once_with(1, "units")


def test_confirmed_install_only_starts_the_existing_downloader_with_captured_urls(dev, workers, monkeypatch):
    record = release()
    dev._apply_releases([record])
    download = Mock()
    monkeypatch.setattr(dev.app.update_checker, "download_and_run_installer", download)
    monkeypatch.setattr(module, "ask_ok_cancel", lambda *args: True)
    dev._install_selected_release()
    assert download.call_count == 0
    workers.pop()()
    download.assert_called_once_with(record["exe_url"], record["sha_url"])


@pytest.mark.parametrize("close", [False, True])
def test_reset_rechecks_reservation_and_lifetime_after_confirmation(dev, monkeypatch, close):
    reset = Mock()
    monkeypatch.setattr(dev.app, "reset_settings", reset)

    def confirm(*args):
        dev.close() if close else dev.set_busy(True)
        return True

    monkeypatch.setattr(module, "ask_ok_cancel", confirm)
    dev._reset_settings()
    reset.assert_not_called()


@pytest.mark.parametrize("language", ["de", "en"])
def test_settings_open_errors_keep_details_in_logs_and_offer_the_path(dev, monkeypatch, caplog, language):
    dev.app.on_language_change(language)
    messages = []
    monkeypatch.setattr(module.webbrowser, "open", Mock(side_effect=OSError("internal association error")))
    monkeypatch.setattr(module, "show_error", lambda app, title, text: messages.append(text))
    dev._open_settings_file()
    assert str(dev.app.paths.settings_file) in messages[0]
    assert "internal association error" not in messages[0]
    assert "internal association error" in caplog.text


def test_language_and_theme_update_in_place_preserving_view_and_geometry(dev, monkeypatch):
    dev._apply_releases([release(), release("2.0.0")])
    dev.releases_combo.set("2.0.0")
    dev._selection_changed()
    dev._toggle_release_notes()
    dev.window.geometry("850x750+50+100")
    dev.app.root.update()
    window = dev.window
    before = window_bounds(window)
    rendered = []
    original = dev.notes_html.load_html
    monkeypatch.setattr(dev.notes_html, "load_html", lambda html: (rendered.append(html), original(html))[-1])
    dev.app.ui_font.configure(size=18)
    dev.app.on_language_change("en")
    dev.app._on_theme_mode_change("dark")
    dev.app.root.update()
    assert dev.window is window
    assert window_bounds(window) == before
    assert dev.releases_combo.get() == "2.0.0"
    assert dev.release_notes_shown
    assert "font-size:18pt" in rendered[-1]
    assert dev.app._current_theme_colors.field_background in rendered[-1]
    assert str(dev.channel_combo.cget("state")) == "readonly"


def test_theme_change_preserves_the_notes_reading_position(dev):
    record = release()
    record["body"] *= 10
    dev._apply_releases([record])
    dev._toggle_release_notes()
    dev.app.root.update()
    dev.notes_html.yview_moveto(0.5)
    dev.app.root.update()
    before = dev.notes_html.html.yview()[0]
    assert before > 0.4
    dev.app._on_theme_mode_change("dark")
    dev.app.root.update()
    assert abs(dev.notes_html.html.yview()[0] - before) < 0.02


def test_auto_notes_height_grows_and_shrinks_but_manual_geometry_survives(dev):
    before = dev.window.winfo_height()
    dev._toggle_release_notes()
    dev.app.root.update()
    assert dev.window.winfo_height() > before
    dev._toggle_release_notes()
    dev.app.root.update()
    assert dev.window.winfo_height() == before
    dev.window.geometry("850x750+70+120")
    dev.app.root.update()
    manual = window_bounds(dev.window)
    dev._toggle_release_notes()
    dev.app.root.update()
    assert window_bounds(dev.window) == manual
    dev.open()
    dev.app.root.update()
    assert window_bounds(dev.window) == manual


def test_notes_resize_uses_the_current_window_monitor_after_a_manual_move(dev, monkeypatch):
    moved = Bounds(-1800, 100, -1164, 553)
    work = Bounds(-1920, 24, 0, 1040)
    monitor = Mock(return_value=work)
    placement = Mock()
    monkeypatch.setattr(module, "monitor_work_area", monitor)
    monkeypatch.setattr(module, "window_bounds", lambda window: moved)
    monkeypatch.setattr(module, "apply_bounds", placement)
    dev._toggle_release_notes()
    monitor.assert_called_once_with(dev.window)
    assert placement.call_args.args[1].left == moved.left


def test_open_during_application_shutdown_does_not_create_a_new_window(app):
    app._closing = True
    app.dev_tools.open()
    assert app.dev_tools.window is None
    app._closing = False


def test_constrained_work_area_scrolls_without_empty_callback_errors(app, monkeypatch):
    app.ui_font.configure(size=18)
    work = Bounds(0, 24, 500, 450)
    monkeypatch.setattr(module, "monitor_work_area", lambda parent: work)
    app.root.deiconify()
    app.root.update()
    before = window_bounds(app.root)
    app.dev_tools.open()
    dev = app.dev_tools
    app.root.update()
    bounds = window_bounds(dev.window)
    assert work.left <= bounds.left < bounds.right <= work.right
    assert work.top <= bounds.top < bounds.bottom <= work.bottom
    assert dev.content.winfo_reqheight() > dev.canvas.winfo_height()
    errors = []
    monkeypatch.setattr(app.root, "report_callback_exception", lambda *args: errors.append(args))
    app.root.tk.call(dev.scrollbar.cget("command"), "")
    dev.reset_button.focus_force()
    app.root.update()
    assert dev.canvas.yview()[0] > 0
    assert window_bounds(app.root) == before
    assert errors == []
