# Giveaway Monitor — Algorithm Documentation

## Overview

The bot implements a **two-stage system** for giveaway detection and joining:

1. **Activity Monitor** — Detects valid giveaways based on real-time chat quality
2. **Giveaway Session Guard** — Prevents duplicate joins, tracks winners, resets cleanly per giveaway

Both stages run automatically — no per-channel configuration required.

---

## Stage 1: Giveaway Detection (Activity Monitor)

### Trigger Detection
When any chat message matches a `giveaway_triggers` pattern (e.g., `!sorteio`, `!vikingos`), the bot opens a short **monitoring window** to evaluate if this is a real giveaway or noise. The window length is configurable (default: 20s; per-channel `monitor_window_s`).

The window starts only once per channel — subsequent triggers during a live window are collected as data but do not restart the clock.

### Metrics Collected

During the monitoring window, the bot observes all chat messages:
- **window_messages** — Total messages received
- **window_unique** — Count of distinct chatters
- **command_like** — Messages that start with `!` or `#` (participation commands)
- **Baseline metrics** — A rolling window (default 150s) of the channel's typical activity, sampled continuously since bot start

### Quality Score Calculation

After the window closes, the bot computes:

```
baseline_rate = baseline_msg_count / max(baseline_window_s, 1)
window_rate   = window_messages    / max(window_elapsed_s, 1)
rate_ratio = window_rate / max(baseline_rate, 0.2)

unique_ratio = window_unique / max(baseline_unique, 1.0)

command_ratio = command_like_count / max(window_messages, 1.0)

score = (0.6 × rate_ratio) + (0.3 × unique_ratio) + (0.1 × (command_ratio × 5.0))
```

### Adaptive Thresholds

The entry decision adapts based on channel size and giveaway diversity:

```
activity_scale = clamp(baseline_unique / 20, 0.25, 3.0)
  // ~20 baseline unique users  →  scale 1.0  (reference)
  // Small channels (< 20)      →  scale < 1.0  (lower thresholds, more sensitive)
  // Large channels (> 20)      →  scale > 1.0  (higher thresholds, stricter)

diversity_ref_count = max(baseline_unique × 0.15, 1.0)
diversity_factor    = clamp(window_unique / diversity_ref_count, 0.75, 2.0)
  // Measures what fraction of regular viewers participated
  // High diversity → easier threshold; low diversity → stricter

adaptive_threshold = clamp(
  (1.25 × activity_scale) / diversity_factor,
  1.25 × 0.4,    // minimum: 0.5
  1.25 × 2.5     // maximum: 3.125
)
```

### Entry Decision

Hard floors (prevent single-message false positives):
```
ABSOLUTE_MIN_MESSAGES_IN_WINDOW = 2
ABSOLUTE_MIN_UNIQUE_CHATTERS    = 2
```

Scaled gates:
```
adaptive_min_messages = max(2, round(min_messages_in_window × activity_scale))
adaptive_min_unique   = max(2, round(min_unique_chatters   × activity_scale))

ENTER = (window_messages ≥ adaptive_min_messages)
    AND (window_unique   ≥ adaptive_min_unique)
    AND (score           ≥ adaptive_threshold)
```

### Example

**Large channel example (100 unique baseline users):**
- activity_scale = 100/20 = 5.0 → clamped to 3.0
- Giveaway window: 50 messages, 15 unique users, 5 commands
- window_rate = 50/20 = 2.5 msg/s
- baseline_rate = 300/150 = 2.0 msg/s
- rate_ratio = 2.5/2.0 = 1.25
- unique_ratio = 15/100 = 0.15
- command_ratio = 5/50 = 0.1
- **score = (0.6 × 1.25) + (0.3 × 0.15) + (0.1 × 0.1 × 5) = 0.75 + 0.045 + 0.05 = 0.845**
- adaptive_threshold = (1.25 × 3.0) / 1.0 = 3.75 → clamped to 3.125
- **Result: score 0.845 < 3.125 → IGNORE** (too few users participated relative to channel size)

**Small channel example (5 unique baseline users):**
- activity_scale = 5/20 = 0.25
- Giveaway window: 5 messages, 3 unique users, 1 command
- window_rate = 5/20 = 0.25 msg/s
- baseline_rate = 10/150 = 0.067 msg/s
- rate_ratio = 0.25/0.067 = 3.73
- unique_ratio = 3/5 = 0.6
- command_ratio = 1/5 = 0.2
- **score = (0.6 × 3.73) + (0.3 × 0.6) + (0.1 × 0.2 × 5) = 2.238 + 0.18 + 0.1 = 2.518**
- adaptive_threshold = (1.25 × 0.25) / 1.0 = 0.3125
- **Result: score 2.518 > 0.3125 → ENTER** (very active for this small channel)

---

## Stage 2: Giveaway Session Guard

After passing Stage 1, the **session guard** manages the full lifecycle of a giveaway to prevent duplicate joins and detect when it ends.

### Session Lifecycle

Each channel has at most **one active giveaway session** at a time, stored as an in-memory dict with the following fields:

| Field | Type | Description |
|---|---|---|
| `signature` | str | Stable key: `channel\|trigger\|command`. Identifies the giveaway. |
| `active_since` | float | Monotonic timestamp of first join. |
| `last_join_at` | float | Timestamp of the most recent successful join. |
| `last_score` | float | Activity score at the last join. |
| `next_join_score` | float | Minimum score required to re-enter (`last_score × 1.5`). |
| `winner_seen_at` | float | Monotonic timestamp when first winner announcement was seen (0 = not seen yet). |
| `join_count` | int | How many times we have joined this giveaway session. |

### Phase 1: No Active Session — First Join

```
active_session = _get_active_giveaway_session(channel, now)

IF active_session is None:
  → Join freely (Stage 1 already approved)
  → _record_successful_join(channel, signature, now, score):
      session = {
        signature:       "channel|trigger|command",
        active_since:    now,
        last_join_at:    now,
        last_score:      score,
        next_join_score: score × 1.5,   ← geometric gate for any rejoin
        winner_seen_at:  0.0,
        join_count:      1,
      }
```

### Phase 2: Active Session — Rejoin Gate

If a session exists and no winner has been announced yet:

```
required_score = session.next_join_score   (= previous_join_score × 1.5)

IF current_score >= required_score:
  → Rejoin allowed
  → session.last_score      = current_score
  → session.next_join_score = current_score × 1.5   ← bar rises again
  → session.join_count     += 1

ELSE:
  → Blocked: "Active giveaway blocked (score X < next required Y)"
```

Each successful rejoin raises the threshold geometrically: `1.5×`, `2.25×`, `3.375×`, …
This ensures only genuinely stronger activity peaks can re-enter the same giveaway.

### Phase 3: Winner Detected — Countdown to Reset

When any chat message matches a `won_triggers` pattern (regardless of which username it is):

```
_mark_first_winner_seen(channel, now):
  IF session is active AND session.winner_seen_at == 0:
    session.winner_seen_at = now
    → logs "Winner announcement seen - giveaway will close in 300s"
```

After that, all new Stage-1 approvals for this channel are blocked:
```
IF session.winner_seen_at > 0:
  → logs "Giveaway trigger ignored - session ending after winner (Xs until reset)"
  → BLOCK
```

### Phase 4: Session Expiry — Clean Slate

```
_get_active_giveaway_session(channel, now):
  IF session.winner_seen_at > 0
  AND (now - session.winner_seen_at) >= GIVEAWAY_END_AFTER_FIRST_WIN_S (300s):
    → session deleted
    → returns None
    → next trigger starts a brand-new session with a clean score history
```

### Example End-to-End

| Event | Score | Session state |
|---|---|---|
| First `!vikingos` trigger, window approved | 1.05 | CREATED: `next_join_score = 1.575` |
| Second `!vikingos` trigger | 1.30 | BLOCKED (1.30 < 1.575) |
| Third `!vikingos` trigger | 1.60 | ALLOWED (1.60 ≥ 1.575): `next_join_score = 2.40` |
| Fourth `!vikingos` trigger | 2.10 | BLOCKED (2.10 < 2.40) |
| Winner message seen (`"Congratulations, xyz!"`) | — | ENDING countdown starts (300s) |
| Fifth `!vikingos` trigger | 3.00 | BLOCKED (session ENDING) |
| 300s pass | — | Session DELETED — next trigger starts fresh |

---

## Integration: Complete Flow

```
CHAT MESSAGE RECEIVED
│
├─→ Author in ignored list? → discard
│
├─→ Matches won_triggers (any username wildcard)?
│   └─→ YES → _mark_first_winner_seen(channel)
│                (sets session.winner_seen_at if not already set)
│
├─→ Matches won_triggers (own bot username)?
│   └─→ YES → _handle_won_trigger() → send won_prefix reply → DONE
│
├─→ Matches giveaway_triggers?
│   └─→ YES → activity monitor not already running?
│               └─→ start_window(channel)
│
├─→ activity_monitor.evaluate_if_ready(channel)
│   └─→ None   → window still collecting → DONE
│   └─→ IGNORE → log red "Giveaway ignored: [reason]" → DONE
│   └─→ ENTER  → proceed to session guard ↓
│
├─→ SESSION GUARD
│   ├─→ No active session?
│   │   └─→ ALLOW → _record_successful_join() → notify → sleep(delay) → send command
│   │
│   ├─→ Active session, winner_seen_at > 0?
│   │   └─→ BLOCK → log red "Giveaway trigger ignored - session ending after winner (Xs until reset)"
│   │
│   └─→ Active session, no winner yet?
│       ├─→ current_score < next_join_score → BLOCK → log red "Active giveaway blocked..."
│       └─→ current_score ≥ next_join_score → ALLOW → _record_successful_join() → notify → sleep → send
│
└─→ SEND
    ├─→ Windows notification
    ├─→ _record_successful_join()  ← BEFORE sleep (so concurrent triggers see the session)
    ├─→ sleep(random delay 5–25s)
    └─→ channel.send(giveaway_message)
```

> **Race condition guard:** The session is written *before* the random delay sleep. This prevents a second
> trigger arriving during the sleep window from bypassing the guard and sending a duplicate command.


---

## Mathematical Properties

### Score Components (Weighted)

1. **Rate ratio (60% weight):** Measures message burst
   - Window rate compared to baseline
   - Detects sudden influx of messages

2. **Unique ratio (30% weight):** Measures participation breadth
   - Unique users in window vs. baseline
   - Detects community engagement (not bot spam)

3. **Command ratio (10% weight):** Measures giveaway-like behavior
   - Commands/exclamations indicate participation
   - Secondary signal, lowest weight

### Channel-Size Adaptive Scaling

- **Small channels (< 20 baseline unique users):** `activity_scale < 1.0`
  - Lower gates — proportionally smaller absolute activity is still meaningful

- **Large channels (> 20 baseline unique users):** `activity_scale > 1.0`
  - Higher gates — more background noise requires a clearer signal

- **Clamped (0.4 – 2.5× threshold):** Prevents extreme values from breaking the system

### Session Geometric Score Gate

```
next_join_score = last_join_score × 1.5

Rejoin thresholds grow geometrically:
  Join 1: score S         → next required: S × 1.5
  Join 2: score S'        → next required: S' × 1.5
  Join 3: score S''       → next required: S'' × 1.5

Example with minimum-passing scores each time:
  Join 1: 1.00 → threshold raises to 1.50
  Join 2: 1.50 → threshold raises to 2.25
  Join 3: 2.25 → threshold raises to 3.375
```

Each valid rejoin requires genuinely more intense chat activity than the last.

---

## Configuration

### Global defaults (`config.json` → `activity_monitor`)

| Key | Default | Description |
|---|---|---|
| `baseline_window_s` | 150 | Rolling baseline accumulation window (seconds) |
| `monitor_window_s` | 20 | Giveaway spike detection window (seconds) |
| `min_messages_in_window` | 3 | Minimum message count gate (before scaling) |
| `min_unique_chatters` | 3 | Minimum unique chatter gate (before scaling) |
| `enter_score_threshold` | 1.25 | Score gate (before adaptive scaling) |

Any of these can be overridden per-channel with an `activity_monitor` block in that channel's config entry.
Hard floors `ABSOLUTE_MIN_MESSAGES_IN_WINDOW = 2` and `ABSOLUTE_MIN_UNIQUE_CHATTERS = 2` always apply.

### Giveaway session constants (in `bot.py`)

| Constant | Value | Description |
|---|---|---|
| `GIVEAWAY_END_AFTER_FIRST_WIN_S` | 300.0 | Seconds after first winner seen until session expires |
| Rejoin multiplier | 1.5× | Each successful join raises the score bar by this factor |

---

## Logging & Observability

All decisions are logged for transparency. Color coding is applied in the terminal/GUI.

**Giveaway detection (Stage 1):**
```
[account] [channel] Giveaway ignored: insufficient activity [score=0.45<0.50, volume=2<3] (activity_scale=0.25 diversity=2.0)   ← red
[account] [channel] Windows notification sent: Giveaway detected in channel!   ← light blue
[account] [channel] Decision: score=1.017 >= threshold=0.5 volume=6/2 unique=4/2 (activity_scale=0.3 diversity=2.0)   ← light blue
```

**Session guard (Stage 2):**
```
[account] [channel] Giveaway ACTIVE (score=1.017, next_threshold=1.526)   ← purple  (first join only)
[account] [channel] Active giveaway blocked (score 1.20 < next required 1.526)   ← red
[account] [channel] Active giveaway rejoin allowed (1.60 >= 1.526, last join score 1.017)   ← dim
[account] [channel] Winner announcement seen - giveaway will close in 300s   ← purple
[account] [channel] Giveaway trigger ignored - session ending after winner (287s until reset)   ← red
```

**Won reply:**
```
[account] [channel] Won giveaway                                                    ← green
[account] [channel] Message: <matched message>                                      ← green
[account] [channel] Sending: <won_reply>                                            ← white
[account] [channel] Won trigger from sender on cooldown (120s left) - skipping      ← yellow
```

---

## Summary

| Stage | Input | Logic | Output |
|---|---|---|---|
| **Trigger detection** | Message matches `giveaway_triggers` | Open monitoring window | — |
| **Quality evaluation** | Window closes | Adaptive score + absolute floors + scaled gates | ENTER or IGNORE |
| **Session guard — first join** | ENTER, no active session | Always allow | Create session, send command |
| **Session guard — rejoin** | ENTER, session active, no winner | `score ≥ next_join_score`? | ALLOW or BLOCK |
| **Session guard — ENDING** | ENTER, winner seen | Always block | BLOCK + log countdown |
| **Session expiry** | 300s elapsed since winner | Delete session | Fresh start for next giveaway |
| **Won reply** | `won_triggers` matches own username | Cooldown check | Send `won_prefix` reply |
