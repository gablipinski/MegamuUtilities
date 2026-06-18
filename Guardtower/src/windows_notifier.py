from __future__ import annotations

import shutil
import subprocess
import sys
import webbrowser
from typing import Callable
from xml.sax.saxutils import escape

from config import NotificationConfig
from console_log import log_line

# PowerShell AUMID — already registered by Windows, guarantees pop-up delivery
# without any COM/AUMID registration from the app side.
_PS_AUMID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"


def _sanitize_xml_text(value: str) -> str:
    """Remove codepoints not allowed by XML 1.0.

    Twitch/GUI text can occasionally contain control characters that make
    XmlDocument.LoadXml fail. Filtering these keeps toast delivery reliable.
    """

    allowed_chars: list[str] = []
    for ch in value:
        cp = ord(ch)
        if cp in (0x9, 0xA, 0xD) or (0x20 <= cp <= 0xD7FF) or (0xE000 <= cp <= 0xFFFD) or (0x10000 <= cp <= 0x10FFFF):
            allowed_chars.append(ch)
    return "".join(allowed_chars)


class DesktopNotificationService:
    """Desktop toast service.

    Delivery strategy:
    - Primary: PowerShell subprocess using Windows' registered PowerShell AUMID.
      This guarantees pop-up delivery on any workspace without COM registration.
    - Fallback: winsdk WinRT direct call (works when AUMID is recognized).
    - Click actions (callables) cannot be invoked from the toast itself without
      COM registration; instead the toast body instructs the user to switch to
      Guardtower, and taskbar flashing draws attention.
    """

    def __init__(self, app_id: str = "Guardtower") -> None:
        self.app_id = app_id

    def _send_via_powershell(
        self,
        title: str,
        message: str,
        hint: str | None = None,
    ) -> tuple[bool, str | None]:
        if sys.platform != "win32":
            return False, "not Windows"

        ps = shutil.which("powershell") or shutil.which("pwsh")
        if ps is None:
            return False, "powershell not found"

        safe_title = _sanitize_xml_text(title)
        safe_message = _sanitize_xml_text(message)
        safe_hint = _sanitize_xml_text(hint) if hint else None

        lines = [
            f"<text>{escape(safe_title)}</text>",
            f"<text>{escape(safe_message)}</text>",
        ]
        if safe_hint:
            lines.append(f"<text>{escape(safe_hint)}</text>")

        inner = "".join(lines)
        xml_str = (
            f'<toast duration="long"><visual>'
            f'<binding template="ToastGeneric">{inner}</binding>'
            f"</visual></toast>"
        )

        script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('{xml_str.replace("'", "''")}')
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{_PS_AUMID}').Show($toast)
"""
        try:
            completed = subprocess.run(
                [
                    ps,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-WindowStyle",
                    "Hidden",
                    "-NonInteractive",
                    "-Command",
                    script,
                ],
                creationflags=subprocess.CREATE_NO_WINDOW
                if sys.platform == "win32"
                else 0,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=6,
            )
            if completed.returncode != 0:
                err = (completed.stderr or completed.stdout or "powershell returned non-zero").strip()
                return False, err[:300]
            return True, None
        except Exception as exc:
            return False, str(exc)

    def _send_via_winsdk(
        self,
        title: str,
        message: str,
        hint: str | None = None,
    ) -> tuple[bool, str | None]:
        try:
            from winsdk.windows.data.xml.dom import XmlDocument
            from winsdk.windows.ui.notifications import (
                ToastNotification,
                ToastNotificationManager,
            )
        except Exception:
            return False, "winsdk unavailable"

        try:
            safe_title = _sanitize_xml_text(title)
            safe_message = _sanitize_xml_text(message)
            safe_hint = _sanitize_xml_text(hint) if hint else None

            hint_line = f"<text>{escape(safe_hint)}</text>" if safe_hint else ""
            toast_xml = (
                '<toast duration="long"><visual><binding template="ToastGeneric">'
                f"<text>{escape(safe_title)}</text>"
                f"<text>{escape(safe_message)}</text>"
                f"{hint_line}"
                "</binding></visual></toast>"
            )
            xml_doc = XmlDocument()
            xml_doc.load_xml(toast_xml)
            toast = ToastNotification(xml_doc)
            notifier = ToastNotificationManager.create_toast_notifier(_PS_AUMID)
            notifier.show(toast)
            return True, None
        except Exception as exc:
            return False, str(exc)

    def send_basic(self, title: str, message: str) -> tuple[bool, str | None]:
        ok, err = self._send_via_powershell(title, message)
        if ok:
            return True, None
        return self._send_via_winsdk(title, message)

    def send_action(
        self,
        title: str,
        message: str,
        *,
        action_label: str,
        action: Callable[[], None] | str,
    ) -> tuple[bool, str | None]:
        # Callable click handlers cannot be invoked from a toast without COM
        # registration. Embed the instruction in the toast body instead, and
        # rely on taskbar flashing to guide the user back to Guardtower.
        hint = "Switch to Guardtower to respond"
        ok, err = self._send_via_powershell(title, message, hint)
        if ok:
            return True, None
        return self._send_via_winsdk(title, message, hint)


class WindowsNotifier:
    """Sends Windows toast notifications for runtime bot events."""

    def __init__(self, config: NotificationConfig):
        self.config = config
        self._warned = False
        self._service = DesktopNotificationService(app_id="Guardtower")

    def send_notification(
        self,
        channel: str,
        message: str,
        title: str = "Giveaway detected!",
        account: str | None = None,
        launch_url: str | None = None,
    ) -> bool:
        """Sends a Windows toast notification."""

        if not self.config.enabled:
            return False

        body = self.config.message.format(
            channel=channel,
            message=message[:50] + "..." if len(message) > 50 else message,
        )

        if launch_url:
            ok, err = self._service.send_action(
                title,
                body,
                action_label="Open Link",
                action=lambda: webbrowser.open_new_tab(launch_url),
            )
        else:
            ok, err = self._service.send_basic(title, body)

        if ok:
            log_line(f"Windows notification sent: {body}", "decision", channel, account=account)
            return True

        if not self._warned:
            log_line(
                f"Windows notifications unavailable ({err or 'unknown backend error'}).",
                "notification",
                channel,
                account=account,
            )
            self._warned = True
        return False
