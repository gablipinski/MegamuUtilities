"""
Textual TUI for the Twitch Giveaway Monitor.

Each Twitch channel gets its own panel showing live log lines and a status
badge (IDLE / ACTIVE / ENDING).  A small system panel at the top shows
startup and non-channel messages.

Launch via:  python main.py --gui
"""

from __future__ import annotations

import asyncio
import subprocess
import time
import argparse
import math

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Footer, Header, RichLog, Static

from console_log import set_gui_hook
from config import BotConfig
from startup_logs import emit_startup_logs
from twitchio.ext import commands  # type: ignore[import]
from bot import TwitchBot


# ---------------------------------------------------------------------------
# Colour map: log kind → Rich markup colour
# ---------------------------------------------------------------------------
_RICH_COLOR: dict[str, str] = {
    "join":             "dim white",
    "monitor_start":    "dim white",
    "ignore":           "bold red",
    "win":              "bold green",
    "notification":     "bright_cyan",
    "send":             "bold white",
    "giveaway_active":  "bold magenta",
    "giveaway_inactive":"magenta",
    "decision":         "bright_blue",
    "cooldown":         "yellow",
    "other":            "dim white",
}

GIVEAWAY_SESSION_DURATION_S = 300.0  # mirrors bot.py constant


# ---------------------------------------------------------------------------
# Internal message: routes a log_line call into the Textual event loop
# ---------------------------------------------------------------------------
class LogEvent(Message):
    def __init__(self, message: str, kind: str, channel: str | None, account: str | None) -> None:
        super().__init__()
        self.log_message = message
        self.kind = kind
        self.channel = channel
        self.account = account


# ---------------------------------------------------------------------------
# Per-channel panel
# ---------------------------------------------------------------------------
def _copy_to_clipboard(text: str) -> bool:
    """Copy text to Windows clipboard. Returns True on success."""
    try:
        subprocess.run(['clip'], input=text, encoding='utf-8', check=True)
        return True
    except Exception:
        return False


class ChannelPanel(Widget):
    """Compact panel for one Twitch channel: header badge + scrollable log."""

    can_focus = True

    BINDINGS = [
        Binding("c", "copy_log", "Copy log"),
    ]

    DEFAULT_CSS = """
    ChannelPanel {
        border: solid $primary-darken-3;
        width: 1fr;
        height: 9;
        margin: 0 1 1 0;
    }
    ChannelPanel:focus {
        border: solid $accent;
    }
    ChannelPanel .ch-header {
        background: $primary-darken-3;
        color: $text-muted;
        height: 1;
        padding: 0 1;
        text-style: bold;
    }
    ChannelPanel .ch-header.status-active {
        background: $success-darken-2;
        color: $text;
    }
    ChannelPanel .ch-header.status-ending {
        background: $warning-darken-2;
        color: $text;
    }
    ChannelPanel RichLog {
        background: #111111;
        scrollbar-size: 1 1;
        height: 1fr;
    }
    """

    _STATUS_IDLE = "idle"
    _STATUS_ACTIVE = "active"
    _STATUS_ENDING = "ending"

    _LOG_BUFFER_MAX = 500

    def __init__(self, channel_name: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.channel_name = channel_name
        self._status = self._STATUS_IDLE
        self._status_since: float = 0.0
        self._log_buffer: list[str] = []
        self._pending_widget_logs: list[tuple[str, str]] = []
        self._session_giveaways = 0
        self._session_wins = 0
        self._session_win_recorded = False

    def _render_header_text(self, now: float | None = None) -> str:
        stats = f"({self._session_giveaways}/{self._session_wins})"
        if self._status == self._STATUS_ACTIVE:
            return f"#{self.channel_name} {stats}   ● ACTIVE"
        if self._status == self._STATUS_ENDING:
            now_value = now if now is not None else time.monotonic()
            remaining = max(0, int(math.ceil(GIVEAWAY_SESSION_DURATION_S - (now_value - self._status_since))))
            return f"#{self.channel_name} {stats}   ⏱ ENDING  {remaining:>3}s"
        return f"#{self.channel_name} {stats}   ○ idle"

    def compose(self) -> ComposeResult:
        yield Static(
            self._render_header_text(),
            classes="ch-header",
            id=f"hdr_{self.channel_name}",
        )
        yield RichLog(
            highlight=False,
            markup=True,
            wrap=True,
            id=f"log_{self.channel_name}",
        )

    def on_mount(self) -> None:
        self._refresh_header()
        self._flush_pending_widget_logs()

    def _refresh_header(self, now: float | None = None) -> None:
        try:
            header = self.query_one(f"#hdr_{self.channel_name}", Static)
        except NoMatches:
            return

        header.remove_class("status-active")
        header.remove_class("status-ending")
        if self._status == self._STATUS_ACTIVE:
            header.add_class("status-active")
        elif self._status == self._STATUS_ENDING:
            header.add_class("status-ending")
        header.update(self._render_header_text(now))

    def _flush_pending_widget_logs(self) -> None:
        if not self._pending_widget_logs:
            return
        try:
            log = self.query_one(f"#log_{self.channel_name}", RichLog)
        except NoMatches:
            return

        pending = self._pending_widget_logs
        self._pending_widget_logs = []
        for pending_message, pending_kind in pending:
            pending_color = _RICH_COLOR.get(pending_kind, "dim white")
            log.write(f"[{pending_color}]{pending_message}[/{pending_color}]")

    def add_log(self, message: str, kind: str) -> None:
        self._log_buffer.append(message)
        if len(self._log_buffer) > self._LOG_BUFFER_MAX:
            self._log_buffer = self._log_buffer[-self._LOG_BUFFER_MAX:]

        color = _RICH_COLOR.get(kind, "dim white")
        try:
            log = self.query_one(f"#log_{self.channel_name}", RichLog)
        except NoMatches:
            self._pending_widget_logs.append((message, kind))
        else:
            if self._pending_widget_logs:
                self._flush_pending_widget_logs()
            log.write(f"[{color}]{message}[/{color}]")

        if kind == "giveaway_active":
            if self._status != self._STATUS_ACTIVE:
                self._session_giveaways += 1
                self._session_win_recorded = False
            self._set_status(self._STATUS_ACTIVE)
        elif kind == "giveaway_inactive":
            self._set_status(self._STATUS_ENDING)
        elif kind == "win" and not self._session_win_recorded:
            self._session_wins += 1
            self._session_win_recorded = True
            self._refresh_header()

    def action_copy_log(self) -> None:
        content = "\n".join(self._log_buffer)
        if _copy_to_clipboard(content):
            self.notify(f"Copied {len(self._log_buffer)} lines from #{self.channel_name}", timeout=2)
        else:
            self.notify("Clipboard copy failed", severity="error", timeout=2)

    def _set_status(self, status: str) -> None:
        self._status = status
        self._status_since = time.monotonic()
        self._refresh_header()

    def tick(self, now: float) -> None:
        """Called by the app timer; resets ENDING → IDLE after session expires."""
        if self._status == self._STATUS_ENDING:
            if now - self._status_since >= GIVEAWAY_SESSION_DURATION_S:
                self._set_status(self._STATUS_IDLE)
                return
            self._refresh_header(now)


# ---------------------------------------------------------------------------
# System / global panel (startup + non-channel messages)
# ---------------------------------------------------------------------------
class SystemPanel(Widget):
    can_focus = True

    BINDINGS = [
        Binding("c", "copy_log", "Copy log"),
        Binding("u", "copy_url", "Copy URL"),
    ]

    DEFAULT_CSS = """
    SystemPanel {
        border: solid $accent-darken-2;
        height: 15;
        margin: 0 1 1 0;
    }
    SystemPanel:focus {
        border: solid $accent;
    }
    SystemPanel .sys-header {
        background: $accent-darken-2;
        height: 1;
        padding: 0 1;
        text-style: bold;
    }
    SystemPanel .sys-link-bar {
        background: #0d1b2a;
        height: 1;
        padding: 0 1;
        color: $accent;
    }
    SystemPanel RichLog {
        background: #111111;
        scrollbar-size: 1 1;
        margin: 0 1 0 1;
    }
    """

    _LOG_BUFFER_MAX = 500

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._log_buffer: list[str] = []
        self._link_url: str = ""

    def compose(self) -> ComposeResult:
        yield Static("⚙  System", classes="sys-header")
        yield Static("MultiTwitch ► (aguardando...)", id="sys_link", classes="sys-link-bar")
        yield RichLog(highlight=False, markup=True, wrap=True, id="sys_log")

    def set_link(self, url: str) -> None:
        self._link_url = url
        self.query_one("#sys_link", Static).update(f"MultiTwitch ► {url}")

    def action_copy_url(self) -> None:
        if self._link_url:
            if _copy_to_clipboard(self._link_url):
                self.notify("MultiTwitch URL copiado", timeout=2)
            else:
                self.notify("Falha ao copiar", severity="error", timeout=2)
        else:
            self.notify("URL nao disponivel ainda", timeout=2)

    def add_log(self, message: str, kind: str) -> None:
        self._log_buffer.append(message)
        if len(self._log_buffer) > self._LOG_BUFFER_MAX:
            self._log_buffer = self._log_buffer[-self._LOG_BUFFER_MAX:]

        color = _RICH_COLOR.get(kind, "dim white")
        log = self.query_one("#sys_log", RichLog)
        log.write(f"[{color}]{message}[/{color}]")

    def action_copy_log(self) -> None:
        content = "\n".join(self._log_buffer)
        if _copy_to_clipboard(content):
            self.notify(f"Copied {len(self._log_buffer)} lines", timeout=2)
        else:
            self.notify("Clipboard copy failed", severity="error", timeout=2)


# ---------------------------------------------------------------------------
# Main Textual application
# ---------------------------------------------------------------------------
class MonitorApp(App):
    TITLE = "Twitch Giveaway Monitor"
    DARK = True

    CSS = """
    Screen {
        background: #0d0d0d;
    }
    #channel-scroll {
        height: 1fr;
        width: 100%;
        overflow-y: auto;
        padding: 0 1;
        scrollbar-size: 1 1;
    }
    #channel-list {
        layout: vertical;
        width: 100%;
        height: auto;
    }
    .channel-row {
        layout: horizontal;
        width: 100%;
        height: auto;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, config: BotConfig, args: argparse.Namespace) -> None:
        super().__init__()
        self._bot_config = config
        self._bot_args = args
        self._channel_panels: dict[str, ChannelPanel] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield SystemPanel(id="sys_panel")
        with VerticalScroll(id="channel-scroll"):
            yield Container(id="channel-list")
        yield Footer()

    def _add_system_log(self, message: str, kind: str) -> None:
        try:
            sys_panel = self.query_one("#sys_panel", SystemPanel)
        except NoMatches:
            print(message)
            return
        sys_panel.add_log(message, kind)

    async def on_mount(self) -> None:
        # Build channel panels inside scrollable two-column rows, static order by name.
        channel_list = self.query_one("#channel-list", Container)
        sorted_channels = sorted(self._bot_config.channels, key=lambda channel: channel.name.casefold())

        for index in range(0, len(sorted_channels), 2):
            row = Horizontal(classes="channel-row")
            await channel_list.mount(row)

            for ch in sorted_channels[index:index + 2]:
                panel = ChannelPanel(ch.name, id=f"cpanel_{ch.name}")
                self._channel_panels[ch.name] = panel
                await row.mount(panel)

        # Hook log_line BEFORE starting the bot
        app_ref = self

        def _hook(message: str, kind: str, channel: str | None, account: str | None) -> None:
            app_ref.post_message(LogEvent(message, kind, channel, account))

        set_gui_hook(_hook)

        # Periodic status ticker (every 1 s) for live ENDING countdown.
        self.set_interval(1.0, self._tick_statuses)

        # Launch bot tasks inside Textual's asyncio loop
        asyncio.get_event_loop().create_task(self._run_bot())

    def on_log_event(self, event: LogEvent) -> None:
        channel = event.channel
        if channel and channel in self._channel_panels:
            self._channel_panels[channel].add_log(event.log_message, event.kind)
        else:
            # Route MultTwitch URL to the dedicated link bar instead of the main log
            if not channel and event.log_message.startswith("Multitwitch: "):
                url = event.log_message[len("Multitwitch: "):]
                try:
                    self.query_one("#sys_panel", SystemPanel).set_link(url)
                except NoMatches:
                    pass
                return
            prefix = f"[{channel}] " if channel else ""
            self._add_system_log(f"{prefix}{event.log_message}", event.kind)

    def _tick_statuses(self) -> None:
        now = time.monotonic()
        for panel in self._channel_panels.values():
            panel.tick(now)

    async def _run_bot(self) -> None:
        try:
            emit_startup_logs(self._bot_config, self._bot_args)

            tasks = []
            for account in self._bot_config.accounts:
                bot = commands.Bot(
                    token=account.oauth_token,
                    nick=account.username,
                    prefix="§",
                    initial_channels=[ch.name for ch in self._bot_config.channels],
                )
                twitch_bot = TwitchBot(
                    bot,
                    self._bot_config,
                    account_name=account.username,
                    account_nickname=account.nickname,
                    ignored_usernames=account.ignored_usernames,
                    log_only_mode=self._bot_args.log_only,
                    enable_logging=(self._bot_args.log or self._bot_args.log_only),
                )
                bot.add_cog(twitch_bot)
                tasks.append(bot.start())
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._add_system_log(f"Bot error: {exc}", "ignore")


def run_gui(config: BotConfig, args: argparse.Namespace) -> None:
    app = MonitorApp(config, args)
    app.run()
