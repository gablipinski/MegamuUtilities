from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Callable


WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1
WM_USER_SINGLE_CLICK_TIMER = 1

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_TIMER = 0x0113
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
IDI_APPLICATION = 32512

NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

SINGLE_CLICK_DELAY_MS = 220


LRESULT = getattr(wintypes, 'LRESULT', ctypes.c_ssize_t)
HCURSOR = getattr(wintypes, 'HCURSOR', wintypes.HANDLE)
HBRUSH = getattr(wintypes, 'HBRUSH', wintypes.HANDLE)


class GUID(ctypes.Structure):
    _fields_ = [
        ('Data1', ctypes.c_uint32),
        ('Data2', ctypes.c_uint16),
        ('Data3', ctypes.c_uint16),
        ('Data4', ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.DWORD),
        ('hWnd', wintypes.HWND),
        ('uID', wintypes.UINT),
        ('uFlags', wintypes.UINT),
        ('uCallbackMessage', wintypes.UINT),
        ('hIcon', wintypes.HICON),
        ('szTip', ctypes.c_wchar * 128),
        ('dwState', wintypes.DWORD),
        ('dwStateMask', wintypes.DWORD),
        ('szInfo', ctypes.c_wchar * 256),
        ('uTimeoutOrVersion', wintypes.UINT),
        ('szInfoTitle', ctypes.c_wchar * 64),
        ('dwInfoFlags', wintypes.DWORD),
        ('guidItem', GUID),
        ('hBalloonIcon', wintypes.HICON),
    ]


class WindowsTrayIcon:
    def __init__(
        self,
        *,
        icon_path: Path,
        tooltip: str,
        on_single_click: Callable[[], None],
        on_double_click: Callable[[], None],
    ) -> None:
        self.icon_path = icon_path
        self.tooltip = tooltip[:127]
        self.on_single_click = on_single_click
        self.on_double_click = on_double_click

        self._thread: threading.Thread | None = None
        self._thread_ready = threading.Event()
        self._started = False

        self._hwnd: int | None = None
        self._hicon: int | None = None
        self._class_name = f'SiegetowerTrayWindowClass-{id(self)}'
        self._wnd_proc_ref = None
        self._icon_added = False

    def start(self) -> bool:
        if self._started:
            return True

        self._thread_ready.clear()
        self._thread = threading.Thread(target=self._thread_main, name='SiegetowerTrayThread', daemon=True)
        self._thread.start()
        self._thread_ready.wait(timeout=2.0)
        self._started = bool(self._hwnd)
        return self._started

    def stop(self) -> None:
        if not self._started:
            return

        hwnd = self._hwnd
        if hwnd:
            ctypes.windll.user32.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0)

        if self._thread is not None:
            self._thread.join(timeout=2.0)

        self._started = False
        self._thread = None
        self._hwnd = None
        self._hicon = None
        self._icon_added = False

    def _thread_main(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.PostQuitMessage.restype = None

        WNDPROCTYPE = ctypes.WINFUNCTYPE(
            LRESULT,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        @WNDPROCTYPE
        def _wnd_proc(hwnd: int, msg: int, w_param: int, l_param: int) -> int:
            hwnd_w = wintypes.HWND(hwnd)
            msg_u = wintypes.UINT(msg)
            wparam_w = wintypes.WPARAM(w_param)
            lparam_w = wintypes.LPARAM(l_param)

            if msg == WM_TIMER and int(w_param) == WM_USER_SINGLE_CLICK_TIMER:
                user32.KillTimer(hwnd, WM_USER_SINGLE_CLICK_TIMER)
                self._emit_single_click()
                return 0

            if msg == WM_TRAYICON:
                if int(l_param) == WM_LBUTTONUP:
                    user32.SetTimer(hwnd, WM_USER_SINGLE_CLICK_TIMER, SINGLE_CLICK_DELAY_MS, None)
                    return 0
                if int(l_param) == WM_LBUTTONDBLCLK:
                    user32.KillTimer(hwnd, WM_USER_SINGLE_CLICK_TIMER)
                    self._emit_double_click()
                    return 0

            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd_w)
                return 0

            if msg == WM_DESTROY:
                self._remove_notify_icon()
                user32.PostQuitMessage(0)
                return 0

            return user32.DefWindowProcW(hwnd_w, msg_u, wparam_w, lparam_w)

        self._wnd_proc_ref = _wnd_proc

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ('style', wintypes.UINT),
                ('lpfnWndProc', WNDPROCTYPE),
                ('cbClsExtra', ctypes.c_int),
                ('cbWndExtra', ctypes.c_int),
                ('hInstance', wintypes.HINSTANCE),
                ('hIcon', wintypes.HICON),
                ('hCursor', HCURSOR),
                ('hbrBackground', HBRUSH),
                ('lpszMenuName', wintypes.LPCWSTR),
                ('lpszClassName', wintypes.LPCWSTR),
            ]

        h_instance = kernel32.GetModuleHandleW(None)
        wnd_class = WNDCLASSW()
        wnd_class.style = 0
        wnd_class.lpfnWndProc = self._wnd_proc_ref
        wnd_class.cbClsExtra = 0
        wnd_class.cbWndExtra = 0
        wnd_class.hInstance = h_instance
        wnd_class.hIcon = None
        wnd_class.hCursor = None
        wnd_class.hbrBackground = None
        wnd_class.lpszMenuName = None
        wnd_class.lpszClassName = self._class_name

        atom = user32.RegisterClassW(ctypes.byref(wnd_class))
        if not atom:
            self._thread_ready.set()
            return

        hwnd = user32.CreateWindowExW(
            0,
            self._class_name,
            'Siegetower Tray Window',
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            h_instance,
            None,
        )

        if not hwnd:
            user32.UnregisterClassW(self._class_name, h_instance)
            self._thread_ready.set()
            return

        self._hwnd = hwnd
        self._hicon = self._load_icon()
        self._add_notify_icon()
        self._thread_ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        user32.UnregisterClassW(self._class_name, h_instance)

    def _load_icon(self) -> int:
        user32 = ctypes.windll.user32
        icon_handle = 0
        if self.icon_path.exists():
            icon_handle = int(
                user32.LoadImageW(
                    None,
                    str(self.icon_path),
                    IMAGE_ICON,
                    0,
                    0,
                    LR_LOADFROMFILE | LR_DEFAULTSIZE,
                )
            )
        if not icon_handle:
            icon_handle = int(user32.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION)))
        return icon_handle

    def _build_notify_data(self) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = wintypes.HWND(self._hwnd)
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAYICON
        data.hIcon = wintypes.HICON(self._hicon)
        data.szTip = self.tooltip
        return data

    def _add_notify_icon(self) -> None:
        if not self._hwnd or not self._hicon:
            return
        data = self._build_notify_data()
        if ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            self._icon_added = True

    def _remove_notify_icon(self) -> None:
        if not self._hwnd or not self._icon_added:
            return
        data = self._build_notify_data()
        ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
        self._icon_added = False

    def _emit_single_click(self) -> None:
        try:
            self.on_single_click()
        except Exception:
            pass

    def _emit_double_click(self) -> None:
        try:
            self.on_double_click()
        except Exception:
            pass
