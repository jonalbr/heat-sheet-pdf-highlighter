"""
Main application entry point and coordination
"""
import os
import time
from tkinter import Tk
from .gui.main_window import PDFHighlighterApp


def _is_screenshot_mode() -> bool:
    return os.getenv("HSPH_SCREENSHOT_MODE") in ("1", "true", "True")


def _move_offscreen(root: Tk) -> None:
    try:
        root.geometry("+10000+10000")
    except Exception as e:
        print(f"Error moving window off-screen: {e}")


def _setup_screenshot_state(app: PDFHighlighterApp) -> None:
    """Populate transient UI state used for screenshot mode.

    This only mutates in-memory structures and swallows all exceptions to
    avoid impacting screenshot generation.
    """
    try:
        try:
            app.enable_filter_var.set(1)
            app.names_var.set("Klara Sophie Meier, Jonas Becker-Schmidt, Leon Wagner")
        except Exception as e:
            print(f"Error enabling filter: {e}")

        try:
            app.app_settings.settings["watermark_enabled"] = "True"
            app.app_settings.settings["watermark_text"] = "SGS Hamburg"
            app.app_settings.settings["watermark_color"] = "#FF9F14"
            app.app_settings.settings["watermark_size"] = 20
        except Exception as e:
            print(f"Error setting up screenshot mode: {e}")
    except Exception as e:
        print(f"Error enabling screenshot mode: {e}")


def _preview_target(app: PDFHighlighterApp, root: Tk, target: str, pdf_for_preview: str | None) -> None:
    try:
        if target == "filter":
            app.open_filter_window()
        elif target == "watermark":
            app.open_watermark_window()
        elif target == "devtools":
            app.dev_tools.open()
        elif target == "preview":
            if pdf_for_preview and os.path.exists(pdf_for_preview):
                setattr(app, "input_file_full_path", pdf_for_preview)
                _wm_text = app.app_settings.settings.get("watermark_text") or "SGS Hamburg"
                _wm_color = app.app_settings.settings.get("watermark_color") or "#FF9F14"
                try:
                    _wm_size = int(app.app_settings.settings.get("watermark_size") or 20)
                except Exception as e:
                    print(f"Error getting watermark size: {e}")
                    _wm_size = 20
                _wm_pos = app.app_settings.settings.get("watermark_position") or "bottom"
                app.preview_watermark(
                    enabled=1,
                    text=_wm_text,
                    color=_wm_color,
                    size=_wm_size,
                    position=_wm_pos,
                    preview_page=1,
                    origin=app.root,
                    force_open=True,
                )
    except Exception as e:
        print(f"Error preparing preview target {target}: {e}")


def _capture_and_save(root: Tk, app: PDFHighlighterApp, screenshot_path: str, target: str, delay: float) -> None:
    # Ensure layout is realized
    root.update_idletasks()
    root.update()

    # Prepare requested UI
    _preview_target(app, root, target, os.getenv("HSPH_SCREENSHOT_PDF"))

    if delay > 0:
        time.sleep(delay)
        root.update_idletasks()
        root.update()

    if os.name != "nt":
        return

    try:
        _windows_capture(root, app, screenshot_path, target)
    except Exception as e:
        print(f"Error capturing screenshot: {e}")


def _windows_capture(root: Tk, app: PDFHighlighterApp, screenshot_path: str, target: str) -> None:
    """Windows-specific window capture logic extracted for clarity and testability."""
    import win32gui
    import win32ui
    import win32con
    from PIL import Image, ImageStat

    def _capture_hwnd(hwnd) -> Image.Image | None:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            return None
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        save_bitmap = win32ui.CreateBitmap()
        save_bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(save_bitmap)
        try:
            PW_RENDERFULLCONTENT = 0x00000002
            pw = getattr(win32gui, "PrintWindow", None)
            if pw is not None:
                try:
                    pw(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
                except Exception:
                    save_dc.BitBlt((0, 0), (width, height), mfc_dc, (0, 0), win32con.SRCCOPY)
            else:
                save_dc.BitBlt((0, 0), (width, height), mfc_dc, (0, 0), win32con.SRCCOPY)

            bmpinfo = save_bitmap.GetInfo()
            bmpstr = save_bitmap.GetBitmapBits(True)
            img = Image.frombuffer(
                "RGB",
                (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                bmpstr,
                "raw",
                "BGRX",
                0,
                1,
            )
            return img
        finally:
            win32gui.DeleteObject(save_bitmap.GetHandle())
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwnd_dc)

    def _current_target_hwnd():
        target_map = {
            "filter": ("filter_dialog", "window"),
            "watermark": ("watermark_dialog", "window"),
            "devtools": ("dev_tools", "window"),
            "preview": ("preview_window_handler", "window"),
        }

        try:
            names = target_map.get(target)
            if not names:
                return root.winfo_id()

            parent_attr, win_attr = names
            parent = getattr(app, parent_attr, None)
            if parent is None:
                return root.winfo_id()

            win = getattr(parent, win_attr, None)
            if win is None:
                return root.winfo_id()

            try:
                return win.winfo_id()
            except Exception as e:
                print(f"Error getting {target} window ID: {e}")
                return root.winfo_id()
        except Exception as e:
            print(f"Error getting current target HWND: {e}")
            return root.winfo_id()

    hwnd_target = _current_target_hwnd()
    img = _capture_hwnd(hwnd_target)
    needs_retry = False
    if img is None:
        needs_retry = True
    else:
        stats = ImageStat.Stat(img)
        if all(mn == 255 and mx == 255 for (mn, mx) in [tuple(p) for p in stats.extrema]):
            needs_retry = True

    if needs_retry:
        try:
            root.attributes("-topmost", True)
        except Exception as e:
            print(f"Error setting window to topmost: {e}")
        root.geometry("+10+10")
        root.deiconify()
        root.update_idletasks()
        time.sleep(0.2)
        root.update()
        hwnd_target = _current_target_hwnd()
        img = _capture_hwnd(hwnd_target) or img

    if img is not None:
        os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
        img.save(screenshot_path)


def main():
    """Main application entry point."""
    root = Tk()

    if _is_screenshot_mode():
        _move_offscreen(root)

    app = PDFHighlighterApp(root)

    if _is_screenshot_mode():
        _setup_screenshot_state(app)

    screenshot_path = os.getenv("HSPH_SCREENSHOT_PATH")
    if screenshot_path:
        try:
            target = os.getenv("HSPH_SCREENSHOT_TARGET") or "main"
            delay = float(os.getenv("HSPH_SCREENSHOT_DELAY") or 0)
            _capture_and_save(root, app, screenshot_path, target, delay)
        finally:
            try:
                root.destroy()
            except Exception as e:
                print(f"Error destroying root window: {e}")
        return

    root.mainloop()


if __name__ == "__main__":
    main()
