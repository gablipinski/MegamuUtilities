# Watchtower — Distribution Guide

This document explains how to set up, build, and distribute Watchtower to users with hardware-bound license control.

---

## Overview

```
You (Distributor)                    User
─────────────────                    ────
1. Generate keys (once)
2. Build installer
3. Share installer        ──────►   4. Install the app
                                    5. Run app → sees Machine ID
                          ◄──────   6. Sends Machine ID to you
7. Generate license.dat
8. Send license.dat       ──────►   9. Places license.dat in AppData
                                   10. App runs ✓
```

---

## First-Time Setup (do this once)

### 1. Install prerequisites

```powershell
# Python dependencies
.\scripts\setup_venv.ps1

# Inno Setup 6 (for the installer wizard)
# Download from: https://jrsoftware.org/isdl.php
# Install with default settings
```

### 2. Generate your signing keys

```powershell
# Activate venv first
.\scripts\activate_venv.ps1

# Generate RSA-2048 key pair
python tools\generate_keys.py
```

This creates:
- `tools\private_key.pem` — **your secret key, never share this**
- Patches `src\license_manager.py` with the matching public key

> **Backup `tools\private_key.pem` immediately** (USB drive, password manager, cloud vault).  
> If you lose it, you cannot issue new licenses and must regenerate keys — which invalidates ALL existing licenses.

### 3. Build the installer

```powershell
.\scripts\build_exe.ps1
```

This produces:
- `dist\Watchtower.exe` — standalone compiled executable
- `installer_output\Watchtower_Setup_1.0.0.exe` — Windows installer wizard

---

## Distributing to a User

### Step 1 — Share the installer

Send the user `Watchtower_Setup_1.0.0.exe`.  
They run it, click through the wizard, and the app is installed.

### Step 2 — Get their Machine ID

The user launches Watchtower.  
Because they have no license yet, the activation screen appears:

```
┌─────────────────────────────────────────────────┐
│  Watchtower — Activation Required               │
│                                                 │
│  Your Machine ID:                               │
│  ┌────────────────────────────┐                │
│  │   A3F2-9C1B-D47E-8801     │                │
│  └────────────────────────────┘                │
│         [ Copy Machine ID ]                     │
│                                                 │
│  [ Browse for license.dat… ]  [ Exit ]          │
└─────────────────────────────────────────────────┘
```

The user clicks **Copy Machine ID** and sends it to you (e.g. via WhatsApp, Discord, email).

### Step 3 — Generate their license

```powershell
# Activate venv
.\scripts\activate_venv.ps1

# Basic (expires in 1 year)
python tools\generate_license.py A3F2-9C1B-D47E-8801 "Player One"

# Custom expiry date
python tools\generate_license.py A3F2-9C1B-D47E-8801 "Player One" --expiry 2027-06-01

# No expiry (permanent)
python tools\generate_license.py A3F2-9C1B-D47E-8801 "Player One" --expiry never

# Save to a specific file
python tools\generate_license.py A3F2-9C1B-D47E-8801 "Player One" --output licenses\player_one.dat
```

This creates a `license.dat` file signed with your private key.

### Step 4 — Send license.dat

Send the user the generated `license.dat` file.

### Step 5 — User activates

The user has two options:

**Option A — Browse in the app (easiest):**  
Launch the app → click **Browse for license.dat…** → select the file.  
The app copies it automatically and starts.

**Option B — Manual placement:**  
Copy `license.dat` to:
```
%APPDATA%\Watchtower\license.dat
```
(paste that path into Windows Explorer's address bar)

---

## License File Format

A license.dat is a JSON file:

```json
{
  "machine_id": "A3F2-9C1B-D47E-8801",
  "issued_to": "Player One",
  "expiry": "2027-06-01",
  "signature": "<RSA-PSS signature — do not edit>"
}
```

Any modification to the file invalidates the signature and the app will refuse to run.

---

## Revoking a License

There is no revocation mechanism by design (simpler, no server required).  
Your options:

| Scenario | Action |
|---|---|
| User's license expires | Do not renew — just don't issue a new one |
| You want to cut off a user before expiry | Issue a new build with a future `valid_from` check (requires code change) |
| Emergency: revoke everyone | Regenerate keys (`tools\generate_keys.py`) + rebuild + redistribute |

---

## Rebuilding for a New Version

```powershell
# 1. Make your code changes
# 2. Update version in installer\setup.iss  (#define MyAppVersion "1.1.0")
# 3. Rebuild
.\scripts\build_exe.ps1
```

Existing licenses remain valid across versions — the key pair doesn't change unless you regenerate it.

---

## Security Properties

| Attack | Result |
|---|---|
| Copy exe to another machine | Machine ID mismatch → blocked |
| Share `license.dat` with a friend | Machine ID mismatch on their machine → blocked |
| Edit expiry date in `license.dat` | RSA signature invalid → blocked |
| Reverse engineer the exe | Can read public key — but cannot forge signatures without your private key |
| Crack the binary | Very hard (Nuitka native binary, no `.pyc` bundled) |

---

## File Reference

| File | Purpose | Distribute? |
|---|---|---|
| `tools\private_key.pem` | Signs licenses | **NEVER** |
| `tools\generate_keys.py` | Creates key pair | No |
| `tools\generate_license.py` | Creates license.dat per user | No |
| `installer_output\Watchtower_Setup_*.exe` | User installer | **Yes** |
| `license.dat` (generated per user) | User's activation file | **Yes, per user only** |
