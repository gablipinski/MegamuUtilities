from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt


TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"
DEFAULT_BIN_MINUTES = 5
DAY_FOLDER_FMT = "%d_%m_%Y"


@dataclass
class ChatRecord:
    ts: datetime
    channel: str
    author: str
    message: str
    classes: set[str]
    metadata: dict[str, str]


def parse_record(line: str) -> ChatRecord | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None

    ts_raw = payload.get("timestamp")
    channel = str(payload.get("channel", "")).strip()
    author = str(payload.get("author", "")).strip()
    message = str(payload.get("message", ""))
    classes = set(payload.get("classes", [])) if isinstance(payload.get("classes", []), list) else set()
    metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}

    if not ts_raw or not channel:
        return None

    try:
        ts = datetime.strptime(str(ts_raw), TIMESTAMP_FMT)
    except ValueError:
        return None

    return ChatRecord(
        ts=ts,
        channel=channel,
        author=author,
        message=message,
        classes=classes,
        metadata={str(k): str(v) for k, v in metadata.items()},
    )


def load_records_from_jsonl(jsonl_path: Path) -> list[ChatRecord]:
    records: list[ChatRecord] = []
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = parse_record(line)
                if record is not None:
                    records.append(record)
    except OSError:
        return []

    records.sort(key=lambda r: r.ts)
    return records


def floor_to_bin(ts: datetime, bin_minutes: int) -> datetime:
    minute = (ts.minute // bin_minutes) * bin_minutes
    return ts.replace(minute=minute, second=0, microsecond=0)


def safe_channel_from_path(channel_dir_name: str) -> str:
    if channel_dir_name.startswith("channel_"):
        return channel_dir_name[len("channel_") :]
    return channel_dir_name


def plot_activity_timeline(records: list[ChatRecord], out_path: Path, bin_minutes: int):
    total_counts: Counter[datetime] = Counter()
    giveaway_counts: Counter[datetime] = Counter()
    won_counts: Counter[datetime] = Counter()

    for rec in records:
        bucket = floor_to_bin(rec.ts, bin_minutes)
        total_counts[bucket] += 1
        if "giveaway_trigger" in rec.classes:
            giveaway_counts[bucket] += 1
        if "won_trigger" in rec.classes:
            won_counts[bucket] += 1

    buckets = sorted(total_counts.keys())
    if not buckets:
        return

    total = [total_counts[b] for b in buckets]
    giveaway = [giveaway_counts[b] for b in buckets]
    won = [won_counts[b] for b in buckets]

    plt.figure(figsize=(14, 6))
    plt.plot(buckets, total, label="Total chat messages", linewidth=2)
    plt.plot(buckets, giveaway, label="Giveaway trigger messages", linewidth=2)
    plt.plot(buckets, won, label="Won trigger messages", linewidth=2)
    plt.title(f"Chat Activity vs Trigger Activity ({bin_minutes}-minute bins)")
    plt.xlabel("Time")
    plt.ylabel("Message count")
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


def plot_class_distribution(records: list[ChatRecord], out_path: Path):
    class_counter: Counter[str] = Counter()
    for rec in records:
        for cls in rec.classes:
            class_counter[cls] += 1

    if not class_counter:
        return

    labels, values = zip(*class_counter.most_common())

    plt.figure(figsize=(11, 6))
    plt.bar(labels, values)
    plt.title("Message Class Distribution")
    plt.xlabel("Class")
    plt.ylabel("Count")
    plt.xticks(rotation=25, ha="right")
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


def plot_trigger_text_frequency(records: list[ChatRecord], out_path: Path, top_n: int = 12):
    trigger_counter: Counter[str] = Counter()

    for rec in records:
        if "matched_giveaway_trigger" in rec.metadata:
            trigger_counter[f"giveaway: {rec.metadata['matched_giveaway_trigger']}"] += 1
        if "matched_won_trigger" in rec.metadata:
            trigger_counter[f"won: {rec.metadata['matched_won_trigger']}"] += 1

    if not trigger_counter:
        return

    top = trigger_counter.most_common(top_n)
    labels = [label for label, _ in top]
    values = [count for _, count in top]

    plt.figure(figsize=(14, 7))
    plt.barh(labels, values)
    plt.title("Most Frequent Matched Trigger Texts")
    plt.xlabel("Count")
    plt.ylabel("Trigger text")
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


def plot_hourly_trigger_rate(records: list[ChatRecord], out_path: Path):
    hourly_total: Counter[int] = Counter()
    hourly_trigger: Counter[int] = Counter()

    for rec in records:
        hour = rec.ts.hour
        hourly_total[hour] += 1
        if "giveaway_trigger" in rec.classes or "won_trigger" in rec.classes:
            hourly_trigger[hour] += 1

    hours = list(range(24))
    totals = [hourly_total[h] for h in hours]
    triggers = [hourly_trigger[h] for h in hours]
    rate_pct = [(triggers[i] / totals[i]) * 100.0 if totals[i] else 0.0 for i in range(len(hours))]

    fig, ax1 = plt.subplots(figsize=(14, 6))
    ax1.bar(hours, totals, alpha=0.5, label="Total messages")
    ax1.bar(hours, triggers, alpha=0.8, label="Trigger messages")
    ax1.set_ylabel("Count")
    ax1.set_xlabel("Hour")
    ax1.set_xticks(hours)

    ax2 = ax1.twinx()
    ax2.plot(hours, rate_pct, color="red", marker="o", label="Trigger rate (%)")
    ax2.set_ylabel("Trigger rate (%)")

    fig.suptitle("Hourly Trigger Volume and Trigger Rate")
    handles1, labels1 = ax1.get_legend_handles_labels()
    handles2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(handles1 + handles2, labels1 + labels2, loc="upper left")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def plot_hourly_heatmap(records: list[ChatRecord], out_path: Path):
    if not records:
        return

    matrix = [[0 for _ in range(24)]]

    for rec in records:
        if "giveaway_trigger" not in rec.classes and "won_trigger" not in rec.classes:
            continue
        matrix[0][rec.ts.hour] += 1

    plt.figure(figsize=(14, 3.5))
    plt.imshow(matrix, aspect="auto", interpolation="nearest")
    plt.colorbar(label="Trigger messages")
    plt.title("Trigger Message Heatmap by Hour")
    plt.xlabel("Hour of day")
    plt.ylabel("Day")
    plt.xticks(list(range(24)))
    day_label = records[0].ts.strftime("%Y-%m-%d")
    plt.yticks([0], [day_label])
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()


def write_day_summary(channel_name: str, day_folder: str, records: list[ChatRecord], out_path: Path):
    authors = {rec.author for rec in records}
    giveaway_count = sum(1 for rec in records if "giveaway_trigger" in rec.classes)
    won_count = sum(1 for rec in records if "won_trigger" in rec.classes)
    command_like_count = sum(1 for rec in records if "command_like" in rec.classes)

    payload = {
        "generated_at": datetime.now().strftime(TIMESTAMP_FMT),
        "channel": channel_name,
        "day_folder": day_folder,
        "total_messages": len(records),
        "unique_authors": len(authors),
        "giveaway_trigger_messages": giveaway_count,
        "won_trigger_messages": won_count,
        "command_like_messages": command_like_count,
        "trigger_rate_pct": round(((giveaway_count + won_count) / len(records)) * 100.0, 3) if records else 0.0,
        "first_timestamp": records[0].ts.strftime(TIMESTAMP_FMT) if records else None,
        "last_timestamp": records[-1].ts.strftime(TIMESTAMP_FMT) if records else None,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def build_day_charts(channel_name: str, day_folder: str, records: list[ChatRecord], out_dir: Path, bin_minutes: int):
    out_dir.mkdir(parents=True, exist_ok=True)

    plot_activity_timeline(
        records,
        out_dir / "activity_vs_triggers_timeline.png",
        bin_minutes=bin_minutes,
    )
    plot_class_distribution(records, out_dir / "message_class_distribution.png")
    plot_trigger_text_frequency(records, out_dir / "trigger_text_frequency.png")
    plot_hourly_trigger_rate(records, out_dir / "hourly_trigger_rate.png")
    plot_hourly_heatmap(records, out_dir / "trigger_heatmap_hour.png")
    write_day_summary(channel_name, day_folder, records, out_dir / "analytics_summary.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate per-channel, per-day chat activity charts from logs/chat_monitor."
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Optional day folder in DD_MM_YYYY format (e.g., 21_03_2026).",
    )
    return parser.parse_args()


def validate_day_folder(day_value: str) -> bool:
    try:
        datetime.strptime(day_value, DAY_FOLDER_FMT)
        return True
    except ValueError:
        return False


def main():
    args = parse_args()
    project_root = Path(__file__).resolve().parent.parent.parent
    logs_root = project_root / "logs"
    chat_monitor_root = logs_root / "chat_monitor"

    if not chat_monitor_root.exists():
        print(f"[!] Chat monitor directory not found: {chat_monitor_root}")
        return

    if args.date is not None and not validate_day_folder(args.date):
        print(f"[!] Invalid --date value '{args.date}'. Expected format: DD_MM_YYYY")
        return

    day_jsonls = sorted(chat_monitor_root.glob("channel_*/*/messages.jsonl"))
    if not day_jsonls:
        print("[!] No messages.jsonl files found under logs/chat_monitor.")
        return

    if args.date is not None:
        day_jsonls = [path for path in day_jsonls if path.parent.name == args.date]
        if not day_jsonls:
            print(f"[!] No messages.jsonl files found for date {args.date}.")
            return

    for jsonl_path in day_jsonls:
        day_dir = jsonl_path.parent
        channel_dir = day_dir.parent
        day_folder = day_dir.name
        channel_name = safe_channel_from_path(channel_dir.name)

        records = load_records_from_jsonl(jsonl_path)
        if not records:
            continue

        build_day_charts(
            channel_name=channel_name,
            day_folder=day_folder,
            records=records,
            out_dir=day_dir,
            bin_minutes=DEFAULT_BIN_MINUTES,
        )
        print(f"[✓] Charts generated for channel={channel_name} day={day_folder}")

    if args.date is not None:
        print(f"\n[✓] Analytics generation complete for date {args.date}.")
    else:
        print("\n[✓] Analytics generation complete.")


if __name__ == "__main__":
    main()
