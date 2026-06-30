# PointerScanner

PointerScanner is a standalone project for the pointer finder app migrated from Watchtower tooling.

## Project layout

- `src/pointer_finder.py` - GUI and CLI pointer scanner app
- `scripts/setup_venv.ps1` - creates venv and installs dependencies
- `scripts/activate_venv.ps1` - activates venv only
- `scripts/run_pointer_scanner.ps1` - runs app in GUI mode (or CLI with `-Cli`)

## Quick start

1. Open PowerShell in this folder.
2. Run `./scripts/setup_venv.ps1`.
3. Run `./scripts/run_pointer_scanner.ps1`.

To run in CLI mode:

`./scripts/run_pointer_scanner.ps1 -Cli`

## Optional pointer list workflow

You can optionally load a Python file that defines `SCAN_ADDRESSES` to guide scans after a game update.

Example format:

```python
SCAN_ADDRESSES = [
	{
		"name": "SLDetection",
		"type": "pointer",
		"module": "UnityPlayer.dll",
		"base_offset": "0x01D1C1F0",
		"offsets": ["0x160", "0x80", "0x1E8"],
		"description": "Example",
	}
]
```

Behavior:

- If a pointer list is loaded, the scanner first runs a guided scan using those pointer entries.
- If guided scan finds no match, it automatically falls back to a normal full memory scan.
- If no pointer list is loaded, scanning works as before (from scratch).

GUI:

- Use **Open Pointer List (.py)** in the known pointer section.
- Choose one entry or keep **(all loaded pointers)**.
- After candidate addresses are found, you can use **Test Write** to force a value into the selected candidate address for quick in-game validation.
	- Enter a value in **Test write value** (or leave it empty to reuse refine/current value).
	- If write access is unavailable, run PointerScanner as Administrator and re-attach.

CLI:

```powershell
./scripts/run_pointer_scanner.ps1 -Cli -AppArgs @("--pointer-list-file", ".\\path\\to\\scan_addresses.py")
```

Optional filter by pointer name:

```powershell
./scripts/run_pointer_scanner.ps1 -Cli -AppArgs @("--pointer-list-file", ".\\path\\to\\scan_addresses.py", "--pointer-name", "SLDetection")
```
