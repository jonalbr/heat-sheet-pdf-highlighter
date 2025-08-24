"""
Main application entry point and coordination
"""
import os
import time
from tkinter import Tk
from .gui.main_window import PDFHighlighterApp


def main():
    """Main application entry point."""
    root = Tk()
    # In screenshot mode, move window off-screen to avoid flashing on user's display
    if os.getenv("HSPH_SCREENSHOT_MODE") in ("1", "true", "True"):
        try:
            root.geometry("+10000+10000")
        except Exception as e:
            print(f"Error moving window off-screen: {e}")
    app = PDFHighlighterApp(root)
    # If screenshot mode, populate some transient sample data for better screenshots
    if os.getenv("HSPH_SCREENSHOT_MODE") in ("1", "true", "True"):
        try:
            # Enable filter and add example names (transient only)
            try:
                app.enable_filter_var.set(1)
                app.names_var.set("Klara Sophie Meier, Jonas Becker-Schmidt, Leon Wagner")
            except Exception as e:
                print(f"Error enabling filter: {e}")

            # Enable watermarking in-memory for preview/dialogs without persisting
            try:
                # Some dialogs read from app_settings; but we avoid updating settings on-disk.
                app.app_settings.settings["watermark_enabled"] = "True"
                app.app_settings.settings["watermark_text"] = "SGS Hamburg"
                app.app_settings.settings["watermark_color"] = "#FF9F14"
                app.app_settings.settings["watermark_size"] = 20
                #app.app_settings.settings["watermark_position"] = "bottom"
            except Exception as e:
                print(f"Error setting up screenshot mode: {e}")
        except Exception as e:
            # Do not crash screenshot flow for any UI state errors
            print(f"Error enabling screenshot mode: {e}")
    # If screenshot mode is enabled with a target path, render once, capture, and exit
    screenshot_path = os.getenv("HSPH_SCREENSHOT_PATH")
    if screenshot_path:
        try:
            # Ensure geometry/layout is realized before capture
            root.update_idletasks()
            root.update()
            # Optionally prepare target window/dialogs before capture
            target = os.getenv("HSPH_SCREENSHOT_TARGET") or "main"
            pdf_for_preview = os.getenv("HSPH_SCREENSHOT_PDF")
            delay = float(os.getenv("HSPH_SCREENSHOT_DELAY") or 0)
            # Open requested target UI
            if target == "filter":
                try:
                    app.open_filter_window()
                except Exception as e:
                    print(f"Error opening filter window: {e}")
            elif target == "watermark":
                try:
                    app.open_watermark_window()
                except Exception as e:
                    print(f"Error opening watermark window: {e}")
            elif target == "devtools":
                try:
                    app.dev_tools.open()
                except Exception as e:
                    print(f"Error opening devtools: {e}")
            elif target == "preview":
                try:
                    if pdf_for_preview and os.path.exists(pdf_for_preview):
                        # Pretend user loaded a file so preview can work
                        setattr(app, "input_file_full_path", pdf_for_preview)
                        # Use current settings to preview first page with defaults
                        # Safely coerce settings with defaults
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
                    print(f"Error previewing watermark: {e}")
            # Allow window/dialog to settle
            if delay > 0:
                time.sleep(delay)
                root.update_idletasks()
                root.update()
            # Attempt PrintWindow capture via pywin32
            if os.name == "nt":
                try:
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
                                except Exception as e:
                                    print(f"Error capturing window: {e}")
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
                        # Decide which window to capture based on target
                        try:
                            if target == "filter" and hasattr(app, "filter_dialog"):
                                win = getattr(app.filter_dialog, "window", None)
                                if win is not None:
                                    try:
                                        return win.winfo_id()
                                    except Exception as e:
                                        print(f"Error getting filter window ID: {e}")
                                        return root.winfo_id()
                            if target == "watermark" and hasattr(app, "watermark_dialog"):
                                win = getattr(app.watermark_dialog, "window", None)
                                if win is not None:
                                    try:
                                        return win.winfo_id()
                                    except Exception as e:
                                        print(f"Error getting watermark window ID: {e}")
                                        return root.winfo_id()
                            if target == "devtools" and hasattr(app, "dev_tools"):
                                win = getattr(app.dev_tools, "window", None)
                                if win is not None:
                                    try:
                                        return win.winfo_id()
                                    except Exception as e:
                                        print(f"Error getting devtools window ID: {e}")
                                        return root.winfo_id()
                            if target == "preview" and hasattr(app, "preview_window_handler"):
                                win = getattr(app.preview_window_handler, "window", None)
                                if win is not None:
                                    try:
                                        return win.winfo_id()
                                    except Exception as e:
                                        print(f"Error getting preview window ID: {e}")
                                        return root.winfo_id()
                        except Exception as e:
                            print(f"Error getting current target HWND: {e}")
                        return root.winfo_id()

                    hwnd_target = _current_target_hwnd()
                    # First attempt: off-screen render
                    img = _capture_hwnd(hwnd_target)
                    needs_retry = False
                    if img is None:
                        needs_retry = True
                    else:
                        stats = ImageStat.Stat(img)
                        # Detect a fully white image: min=max=255 per channel
                        if all(mn == 255 and mx == 255 for (mn, mx) in [tuple(p) for p in stats.extrema]):
                            needs_retry = True

                    if needs_retry:
                        # Briefly bring window on-screen and topmost to ensure paint, then recapture
                        try:
                            root.attributes("-topmost", True)
                        except Exception as e:
                            print(f"Error setting window to topmost: {e}")
                        # Move to a corner to reduce notice
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
                except Exception as e:
                    # If pywin32 capture fails, we simply exit without crashing
                    print(f"Error capturing screenshot: {e}")
        finally:
            try:
                root.destroy()
            except Exception as e:
                print(f"Error destroying root window: {e}")
        return

    # Normal interactive run
    root.mainloop()


if __name__ == "__main__":
    main()
