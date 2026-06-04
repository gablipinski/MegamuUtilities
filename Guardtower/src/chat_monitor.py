from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path


class ChatMonitorLogger:
    """Logs per-channel chat messages and classifications for offline analysis."""

    def __init__(self, logs_dir: Path):
        self.base_dir = logs_dir / "chat_monitor"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._summary_cache: dict[str, dict] = {}

    def _sanitize_channel(self, channel_name: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_-]", "_", channel_name)

    def _resolve_paths(self, channel_name: str, timestamp: datetime) -> tuple[Path, Path, Path]:
        safe_channel = self._sanitize_channel(channel_name)
        day_folder = timestamp.strftime("%d_%m_%Y")
        channel_dir = self.base_dir / f"channel_{safe_channel}" / day_folder
        channel_dir.mkdir(parents=True, exist_ok=True)

        text_path = channel_dir / "messages.txt"
        jsonl_path = channel_dir / "messages.jsonl"
        summary_path = channel_dir / "summary.json"
        return text_path, jsonl_path, summary_path

    def _load_summary(self, summary_path: Path, channel_name: str, timestamp: datetime) -> dict:
        key = str(summary_path)
        if key in self._summary_cache:
            return self._summary_cache[key]

        if summary_path.exists():
            try:
                with open(summary_path, "r", encoding="utf-8") as f:
                    summary = json.load(f)
            except (OSError, json.JSONDecodeError):
                summary = {}
        else:
            summary = {}

        summary.setdefault("channel", channel_name)
        summary.setdefault("day", timestamp.strftime("%d_%m_%Y"))
        summary.setdefault("total_messages", 0)
        summary.setdefault("classes", {})
        summary.setdefault("last_updated", timestamp.strftime("%Y-%m-%d %H:%M:%S"))

        self._summary_cache[key] = summary
        return summary

    def log_message(
        self,
        channel_name: str,
        author_name: str,
        message_text: str,
        classes: set[str],
        metadata: dict[str, str] | None = None,
    ):
        now = datetime.now()
        text_path, jsonl_path, summary_path = self._resolve_paths(channel_name, now)

        class_list = sorted(classes)
        class_str = ",".join(class_list) if class_list else "none"

        entry = {
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "channel": channel_name,
            "author": author_name,
            "message": message_text,
            "classes": class_list,
            "metadata": metadata or {},
        }

        with open(text_path, "a", encoding="utf-8") as f:
            f.write(
                f"[{entry['timestamp']}] {author_name}: {message_text} | classes={class_str}"
            )
            if entry["metadata"]:
                f.write(f" | metadata={entry['metadata']}")
            f.write("\n")

        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        summary = self._load_summary(summary_path, channel_name, now)
        summary["total_messages"] = int(summary.get("total_messages", 0)) + 1
        summary_classes = summary.setdefault("classes", {})
        for cls in class_list:
            summary_classes[cls] = int(summary_classes.get(cls, 0)) + 1
        summary["last_updated"] = now.strftime("%Y-%m-%d %H:%M:%S")

        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
