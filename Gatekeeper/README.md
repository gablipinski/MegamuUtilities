# Gatekeeper

Gatekeeper is a local-first license portal for Megamu Utilities products.

It provides:

- User registration and login
- Machine registration using the apps' hardware-bound machine IDs
- User requests for Guardtower, Watchtower, and Siegetower access
- Admin approval and rejection workflow
- Server-side license generation using each product's existing private key
- Installer upload and download per product/version

## Project layout

```text
Gatekeeper/
  main.py
  requirements.txt
  gatekeeper/
    app.py
    config.py
    database.py
    dependencies.py
    license_service.py
    models.py
    routes_admin.py
    routes_auth.py
    routes_user.py
    security.py
    static/
    templates/
```

## Quick start

1. Create a virtual environment.
2. Install dependencies.
3. Set a strong `GATEKEEPER_SECRET_KEY` environment variable.
4. Optionally set bootstrap admin credentials.
5. Run the server.

### PowerShell

```powershell
cd C:\Projects\MegamuUtilities\Gatekeeper
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:GATEKEEPER_SECRET_KEY = "change-this-before-production"
$env:GATEKEEPER_BOOTSTRAP_ADMIN_EMAIL = "admin@example.com"
$env:GATEKEEPER_BOOTSTRAP_ADMIN_PASSWORD = "ChangeMeNow123!"
uvicorn main:app --reload
```

Open `http://127.0.0.1:8000`.

## Docker Compose

Gatekeeper can run in Docker while still reading the sibling product repositories for their private keys.

From [Gatekeeper](c:/Projects/MegamuUtilities/Gatekeeper):

```powershell
docker compose up --build
```

This compose setup:

- builds the Gatekeeper image from the local folder
- mounts `Guardtower`, `Watchtower`, and `Siegetower` read-only under `/workspace`
- stores Gatekeeper runtime data in a named Docker volume
- exposes the portal on `http://127.0.0.1:8000`

Before real use, change these values in [Gatekeeper/docker-compose.yml](c:/Projects/MegamuUtilities/Gatekeeper/docker-compose.yml):

- `GATEKEEPER_SECRET_KEY`
- `GATEKEEPER_BOOTSTRAP_ADMIN_EMAIL`
- `GATEKEEPER_BOOTSTRAP_ADMIN_PASSWORD`
- `GATEKEEPER_ADMIN_ALLOWED_MACS`

To keep Gatekeeper available on LAN while restricting admin login to specific client machines, configure:

- `GATEKEEPER_ENFORCE_ADMIN_MAC=1`
- `GATEKEEPER_ADMIN_ALLOWED_MACS=AA:BB:CC:DD:EE:FF,11:22:33:44:55:66`

Notes:

- This MAC check is applied only for admin accounts.
- Normal user accounts are not blocked by the admin MAC allowlist.
- MAC-based checks rely on ARP visibility and are most reliable on local networks.

Admin 2FA (Google Authenticator-compatible TOTP):

- Admin login always requires a second factor.
- On first admin login, Gatekeeper will require TOTP setup before access is granted.
- On every later admin login, Gatekeeper will require a valid 6-digit TOTP code.
- Any standard TOTP app works (Google Authenticator, Microsoft Authenticator, Authy).

To stop the container:

```powershell
docker compose down
```

To remove the persistent data volume too:

```powershell
docker compose down -v
```

## Default product integration

Gatekeeper is preconfigured to use the sibling product repositories in the MegamuUtilities workspace:

- `Guardtower`
- `Watchtower`
- `Siegetower`

On first startup it seeds these products and points license generation at:

- `licenses/keys/private_key.pem` in each product

## Admin workflow

1. User registers and logs in.
2. User registers one or more machine IDs.
3. Admin uploads the latest installer per product.
4. User submits a license request for a product and machine.
5. Admin approves the request and optionally sets an expiry date.
6. Gatekeeper generates a signed `license.dat` file.
7. User downloads the issued license and the latest installer.

Installer storage policy:

- Each new installer upload replaces previous installers for that product.
- Only one latest installer per product is kept on the server.

## Notes

- This is an MVP suitable for local deployment or an internal VPS.
- Passwords are hashed with PBKDF2-HMAC-SHA256.
- Sessions are cookie-based using Starlette's session middleware.
- SQLite is used by default for fast local setup.
- Installer uploads are stored under `Gatekeeper/data/installers`.
- Generated licenses are stored under `Gatekeeper/data/generated_licenses`.

## Recommended next steps

- Put Gatekeeper behind HTTPS.
- Move SQLite to PostgreSQL for multi-user deployment.
- Add email verification and password reset.
- Add audit logging and rate limiting.
- Add a client API for app-side update checks and license download.
