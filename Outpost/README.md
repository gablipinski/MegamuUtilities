# Outpost

A lightweight Windows process monitor inspired by the same dark Tkinter structure used in the other Megamu Utilities apps.

## Quick start

1. Open PowerShell in the `scripts` directory.
2. Run `./setup_venv.ps1` once.
3. Run `./run_outpost.ps1` to launch the UI.

## Usage

- Click `Attach to Process` and select a running process.
- Fill in the HP and SD pointer configuration values.
- Click `Start Test` to monitor the process until HP or SD reaches zero.
- The app displays the average DPS during the observed interval.
