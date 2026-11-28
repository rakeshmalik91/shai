"""App icon generation and native Windows system tray integration (stdlib only)."""
from __future__ import annotations

import os
import pathlib
import struct
import threading
import zlib

ASSETS_DIR = pathlib.Path(__file__).resolve().parents[2] / "assets"
ICONS_DIR = ASSETS_DIR / "icons"
ICON_ICO_PATH = ICONS_DIR / "shai.ico"
ICON_PNG_PATH = ICONS_DIR / "shai.png"
ICON_MIC_ICO_PATH = ICONS_DIR / "shai_mic.ico"
ICON_MIC_PNG_PATH = ICONS_DIR / "shai_mic.png"


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def _blend(dst: tuple[int, int, int, int], src: tuple[int, int, int, int], alpha: float) -> tuple[int, int, int, int]:
    a = max(0.0, min(1.0, alpha)) * (src[3] / 255.0)
    inv = 1.0 - a
    return (
        int(dst[0] * inv + src[0] * a),
        int(dst[1] * inv + src[1] * a),
        int(dst[2] * inv + src[2] * a),
        min(255, int(dst[3] + (255 - dst[3]) * a)),
    )


def build_shai_icon_png(size: int = 64, listening: bool = False) -> bytes:
    """Render an anti-aliased 64x64 RGBA icon with gradient badge, chevron, and AI spark."""
    sub = 2  # 2x2 supersampling for crisp edges
    rows = []

    for y in range(size):
        row = bytearray()
        for x in range(size):
            r_acc = g_acc = b_acc = a_acc = 0.0
            for sy in range(sub):
                for sx in range(sub):
                    nx = (x + (sx + 0.5) / sub) / float(size)
                    ny = (y + (sy + 0.5) / sub) / float(size)
                    t = (nx + ny) * 0.5

                    # Squircle SDF centered at (0.5, 0.5)
                    px = abs(nx - 0.5) * 2.0
                    py = abs(ny - 0.5) * 2.0
                    squircle = (px ** 4 + py ** 4) ** 0.25

                    if squircle > 0.96:
                        c = (0, 0, 0, 0)
                    else:
                        # Deep obsidian-indigo gradient background
                        bg = (
                            int(_lerp(17, 36, t)),
                            int(_lerp(19, 34, t)),
                            int(_lerp(34, 62, t)),
                            255,
                        )
                        # Neon cyan -> violet border ring
                        if squircle > 0.87:
                            ring = (
                                int(_lerp(56, 192, t)),
                                int(_lerp(189, 132, t)),
                                int(_lerp(248, 252, t)),
                                255,
                            )
                            c = ring
                        else:
                            c = bg
                            # Subtle radial ambient glow behind symbols
                            glow_d = ((nx - 0.5) ** 2 + (ny - 0.5) ** 2) ** 0.5
                            if glow_d < 0.45:
                                glow_a = (1.0 - glow_d / 0.45) ** 2 * 0.28
                                c = _blend(c, (56, 189, 248, 255), glow_a)

                        # Terminal '>' chevron (lower-left / center-left)
                        if 0.20 <= nx <= 0.52 and 0.30 <= ny <= 0.74:
                            arm_x = 0.24 + (0.22 - abs(ny - 0.52)) * 0.95
                            dist = abs(nx - arm_x)
                            if dist <= 0.055:
                                chev_c = (
                                    int(_lerp(125, 166, ny)),
                                    int(_lerp(211, 227, ny)),
                                    int(_lerp(252, 161, ny)),
                                    255,
                                )
                                c = _blend(c, chev_c, 1.0)

                        # 4-point AI spark/star (upper-right)
                        sx_c, sy_c = 0.67, 0.35
                        dx = abs(nx - sx_c)
                        dy = abs(ny - sy_c)
                        if dx < 0.17 and dy < 0.17:
                            # Hyperbolic star shape
                            star = (dx ** 0.65 + dy ** 0.65)
                            if star < 0.23:
                                spark_c = (
                                    int(_lerp(245, 137, t)),
                                    int(_lerp(194, 220, t)),
                                    int(_lerp(231, 235, t)),
                                    255,
                                )
                                c = _blend(c, spark_c, 1.0)

                        # Accent cursor dot/bar at bottom-right
                        if 0.54 <= nx <= 0.76 and 0.65 <= ny <= 0.73:
                            c = _blend(c, (137, 180, 250, 255), 1.0)

                        # Listening mic badge overlay (bottom-right)
                        if listening:
                            bx, by = 0.72, 0.72
                            bd = ((nx - bx) ** 2 + (ny - by) ** 2) ** 0.5
                            if bd <= 0.24:
                                if bd > 0.205:
                                    c = _blend(c, (17, 17, 27, 255), 1.0)
                                else:
                                    c = _blend(c, (243, 139, 168, 255), 1.0)
                                    # White mic capsule + stand inside badge
                                    mx = abs(nx - bx)
                                    my = ny - by
                                    in_capsule = mx <= 0.045 and -0.10 <= my <= 0.02
                                    in_stem = mx <= 0.018 and 0.03 <= my <= 0.10
                                    in_base = mx <= 0.055 and 0.09 <= my <= 0.12
                                    if in_capsule or in_stem or in_base:
                                        c = _blend(c, (255, 255, 255, 255), 1.0)

                    r_acc += c[0]
                    g_acc += c[1]
                    b_acc += c[2]
                    a_acc += c[3]

            denom = float(sub * sub)
            row.extend((
                int(r_acc / denom),
                int(g_acc / denom),
                int(b_acc / denom),
                int(a_acc / denom),
            ))
        rows.append(b"\x00" + bytes(row))

    raw = b"".join(rows)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw, level=9))
        + _png_chunk(b"IEND", b"")
    )


def build_ico_from_png(png_bytes: bytes, size: int = 64) -> bytes:
    """Wrap a PNG image in a single-entry ICO container (supported since Vista+)."""
    header = struct.pack("<HHH", 0, 1, 1)
    dim = 0 if size >= 256 else size
    entry = struct.pack(
        "<BBBBHHII",
        dim, dim, 0, 0, 1, 32, len(png_bytes), 6 + 16,
    )
    return header + entry + png_bytes


def set_windows_app_id(app_id: str = "ShAI") -> None:
    """Set explicit Windows AppUserModelID so taskbar uses Shai's icon, not pythonw.exe."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def ensure_app_icons() -> tuple[pathlib.Path, pathlib.Path]:
    """Ensure assets/icons/shai.png and assets/icons/shai.ico exist on disk."""
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    if not ICON_PNG_PATH.is_file() or not ICON_ICO_PATH.is_file():
        png_bytes = build_shai_icon_png(64, listening=False)
        if not ICON_PNG_PATH.is_file():
            ICON_PNG_PATH.write_bytes(png_bytes)
        if not ICON_ICO_PATH.is_file():
            ICON_ICO_PATH.write_bytes(build_ico_from_png(png_bytes, 64))
    return ICON_ICO_PATH, ICON_PNG_PATH


def ensure_listening_icons() -> tuple[pathlib.Path, pathlib.Path]:
    """Ensure assets/icons/shai_mic.png and assets/icons/shai_mic.ico exist on disk."""
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    if not ICON_MIC_PNG_PATH.is_file() or not ICON_MIC_ICO_PATH.is_file():
        png_bytes = build_shai_icon_png(64, listening=True)
        if not ICON_MIC_PNG_PATH.is_file():
            ICON_MIC_PNG_PATH.write_bytes(png_bytes)
        if not ICON_MIC_ICO_PATH.is_file():
            ICON_MIC_ICO_PATH.write_bytes(build_ico_from_png(png_bytes, 64))
    return ICON_MIC_ICO_PATH, ICON_MIC_PNG_PATH


def format_tray_notification(text: str, max_len: int = 120) -> str:
    """Collapse whitespace and trim text for a Windows tray notification."""
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max(1, max_len - 3)].rstrip() + "..."


class WindowsTrayIcon:
    """Native Windows system tray icon with Show / Restart app / Exit menu."""

    MENU_SHOW = 1001
    MENU_RESTART = 1002
    MENU_EXIT = 1003
    MENU_TOGGLE_LISTENING = 1004
    MENU_SETTINGS = 1005

    def __init__(
        self,
        icon_path: pathlib.Path,
        tooltip: str = "ShAI — AI Coding Assistant",
        on_show=None,
        on_toggle_window=None,
        on_toggle_listening=None,
        on_settings=None,
        on_restart=None,
        on_exit=None,
    ):
        self.icon_path = pathlib.Path(icon_path)
        self.tooltip = tooltip
        self.on_show = on_show
        self.on_toggle_window = on_toggle_window
        self.on_toggle_listening = on_toggle_listening
        self.on_settings = on_settings
        self.on_restart = on_restart
        self.on_exit = on_exit
        self._thread: threading.Thread | None = None
        self._hwnd = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._state_lock = threading.Lock()
        self._listening = False
        self._pending_icon_path: pathlib.Path | None = None
        self._pending_tooltip: str | None = None
        self._pending_notifications: list[tuple[str, str]] = []

    def set_icon(
        self,
        icon_path: pathlib.Path,
        tooltip: str | None = None,
        listening: bool | None = None,
    ) -> None:
        """Update the tray icon, optional tooltip, and listening state on the tray thread."""
        with self._state_lock:
            self._pending_icon_path = pathlib.Path(icon_path)
            if tooltip is not None:
                self._pending_tooltip = tooltip
            if listening is not None:
                self._listening = bool(listening)
        if os.name == "nt" and self._hwnd:
            import ctypes
            ctypes.windll.user32.PostMessageW(self._hwnd, 0x0400 + 21, 0, 0)

    def show_notification(self, title: str, message: str) -> None:
        """Display a trimmed Windows tray balloon/toast notification."""
        trimmed = format_tray_notification(message)
        if not trimmed:
            return
        with self._state_lock:
            self._pending_notifications.append((title or "ShAI", trimmed))
        if os.name == "nt" and self._hwnd:
            import ctypes
            ctypes.windll.user32.PostMessageW(self._hwnd, 0x0400 + 22, 0, 0)

    def start(self) -> bool:
        """Start the tray icon thread on Windows; returns False on other OSes."""
        if os.name != "nt":
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stopped.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)
        return self._hwnd is not None

    def stop(self) -> None:
        """Remove the tray icon and stop its message loop."""
        if os.name != "nt" or self._stopped.is_set():
            return
        self._stopped.set()
        if self._hwnd:
            import ctypes
            user32 = ctypes.windll.user32
            WM_CLOSE = 0x0010
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    def _run_loop(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        kernel32 = ctypes.windll.kernel32

        WM_DESTROY = 0x0002
        WM_COMMAND = 0x0111
        WM_USER = 0x0400
        WM_TRAYICON = WM_USER + 20
        WM_UPDATE_ICON = WM_USER + 21
        WM_SHOW_NOTIFY = WM_USER + 22
        WM_LBUTTONUP = 0x0202
        WM_LBUTTONDBLCLK = 0x0203
        WM_RBUTTONUP = 0x0205
        WM_CONTEXTMENU = 0x007B

        NIM_ADD = 0x00000000
        NIM_MODIFY = 0x00000001
        NIM_DELETE = 0x00000002
        NIF_MESSAGE = 0x00000001
        NIF_ICON = 0x00000002
        NIF_TIP = 0x00000004
        NIF_INFO = 0x00000010
        NIIF_INFO = 0x00000001
        NIIF_NOSOUND = 0x00000010

        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        LR_DEFAULTSIZE = 0x0040

        MF_STRING = 0x0000
        MF_SEPARATOR = 0x0800
        TPM_LEFTALIGN = 0x0000
        TPM_BOTTOMALIGN = 0x0020
        TPM_RIGHTBUTTON = 0x0002

        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
                ("hIconSm", wintypes.HICON),
            ]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("hWnd", wintypes.HWND),
                ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT),
                ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD),
                ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256),
                ("uTimeoutOrVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD),
            ]

        user32.DefWindowProcW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        user32.DefWindowProcW.restype = LRESULT

        hinstance = kernel32.GetModuleHandleW(None)
        class_name = f"ShaiTrayWindow_{id(self)}"
        hicon = user32.LoadImageW(
            None,
            str(self.icon_path),
            IMAGE_ICON,
            0,
            0,
            LR_LOADFROMFILE | LR_DEFAULTSIZE,
        )

        def _show_context_menu(hwnd):
            with self._state_lock:
                is_listening = self._listening
            menu = user32.CreatePopupMenu()
            user32.AppendMenuW(menu, MF_STRING, self.MENU_SHOW, "Open ShAI")
            if self.on_toggle_listening:
                listen_label = "Stop listening" if is_listening else "Start listening"
                user32.AppendMenuW(menu, MF_STRING, self.MENU_TOGGLE_LISTENING, listen_label)
            if self.on_settings:
                user32.AppendMenuW(menu, MF_STRING, self.MENU_SETTINGS, "Settings")
            user32.AppendMenuW(menu, MF_STRING, self.MENU_RESTART, "Restart app")
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, self.MENU_EXIT, "Exit")
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            # SetForegroundWindow can hang if process doesn't have foreground rights;
            # use a non-blocking approach instead
            try:
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
            user32.TrackPopupMenu(
                menu,
                TPM_LEFTALIGN | TPM_BOTTOMALIGN | TPM_RIGHTBUTTON,
                pt.x,
                pt.y,
                0,
                hwnd,
                None,
            )
            user32.DestroyMenu(menu)

        def _wnd_proc(hwnd, msg, wparam, lparam):
            nonlocal hicon
            if msg == WM_TRAYICON:
                event = lparam & 0xFFFF
                if event == WM_LBUTTONUP:
                    if self.on_toggle_window:
                        try:
                            self.on_toggle_window()
                        except Exception:
                            pass
                    elif self.on_show:
                        try:
                            self.on_show()
                        except Exception:
                            pass
                elif event == WM_LBUTTONDBLCLK and not self.on_toggle_window:
                    if self.on_show:
                        try:
                            self.on_show()
                        except Exception:
                            pass
                elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    try:
                        _show_context_menu(hwnd)
                    except Exception:
                        pass
                return 0
            if msg == WM_UPDATE_ICON:
                with self._state_lock:
                    new_path = self._pending_icon_path
                    new_tip = self._pending_tooltip
                    self._pending_icon_path = None
                    self._pending_tooltip = None
                mod = NOTIFYICONDATAW()
                mod.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
                mod.hWnd = hwnd
                mod.uID = 1
                flags = 0
                if new_path is not None:
                    new_hicon = user32.LoadImageW(
                        None, str(new_path), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE
                    )
                    if new_hicon:
                        if hicon:
                            user32.DestroyIcon(hicon)
                        hicon = new_hicon
                        mod.hIcon = hicon
                        flags |= NIF_ICON
                if new_tip is not None:
                    mod.szTip = new_tip[:127]
                    flags |= NIF_TIP
                if flags:
                    mod.uFlags = flags
                    shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(mod))
                return 0
            if msg == WM_SHOW_NOTIFY:
                with self._state_lock:
                    items = list(self._pending_notifications)
                    self._pending_notifications.clear()
                for title, body in items:
                    mod = NOTIFYICONDATAW()
                    mod.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
                    mod.hWnd = hwnd
                    mod.uID = 1
                    mod.uFlags = NIF_INFO
                    mod.szInfoTitle = title[:63]
                    mod.szInfo = body[:255]
                    mod.uTimeoutOrVersion = 4000
                    mod.dwInfoFlags = NIIF_INFO | NIIF_NOSOUND
                    shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(mod))
                return 0
            if msg == WM_COMMAND:
                cmd_id = wparam & 0xFFFF
                if cmd_id == self.MENU_SHOW and self.on_show:
                    try:
                        self.on_show()
                    except Exception:
                        pass
                elif cmd_id == self.MENU_TOGGLE_LISTENING and self.on_toggle_listening:
                    try:
                        self.on_toggle_listening()
                    except Exception:
                        pass
                elif cmd_id == self.MENU_SETTINGS and self.on_settings:
                    try:
                        self.on_settings()
                    except Exception:
                        pass
                elif cmd_id == self.MENU_RESTART and self.on_restart:
                    try:
                        self.on_restart()
                    except Exception:
                        pass
                elif cmd_id == self.MENU_EXIT and self.on_exit:
                    try:
                        self.on_exit()
                    except Exception:
                        pass
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        wndproc_ref = WNDPROC(_wnd_proc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = wndproc_ref
        wc.hInstance = hinstance
        wc.lpszClassName = class_name
        user32.RegisterClassExW(ctypes.byref(wc))

        hwnd = user32.CreateWindowExW(
            0, class_name, "ShaiTray", 0, 0, 0, 0, 0, None, None, hinstance, None
        )
        self._hwnd = hwnd
        self._ready.set()
        if not hwnd:
            return

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = hicon
        nid.szTip = self.tooltip[:127]
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            # Prevent tray thread from crashing silently
            pass
        finally:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            if hicon:
                user32.DestroyIcon(hicon)
            user32.UnregisterClassW(class_name, hinstance)
