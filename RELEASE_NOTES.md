# BinderBridge v0.2.0-alpha.3

BinderBridge v0.2.0-alpha.3 is a first-run setup and self-hosting polish release for the trading-card collection and trade-management app. It focuses on helping a new owner bring a fresh install online with fewer loose ends.

## Highlights

- Walk new owners through public URL, registration policy, SMTP readiness, backups, Scryfall sync, first invites, and first collection import from `Admin -> First-run setup`
- Show recommended defaults for small local groups, including invite-only registration, suspicious-signup review, daily backups, and optional SMTP
- Show a setup-complete banner on the admin dashboard after the first-run wizard is marked complete
- Link directly from setup steps to configuration, deployment, HTTPS/public URL, and backup documentation
- Create copyable manual invite links from the setup wizard when SMTP is not configured
- Use saved public URLs for generated invite and password-recovery links when no environment or config value is set

## Installation

Requires Python 3.10 or newer:

```bash
git clone https://github.com/thefatkid22/BinderBridge.git
cd BinderBridge
git checkout v0.2.0-alpha.3
python app.py
```

Then open `http://127.0.0.1:8000`. The first registered user becomes the owner.

Docker Compose is also supported:

```bash
cp .env.example .env
docker compose up -d --build
```

## Upgrading

1. Create and download a backup from `Admin -> Backup and restore`.
2. Stop BinderBridge.
3. Update the checkout to `v0.2.0-alpha.3`.
4. Start BinderBridge. SQLite migrations run automatically.
5. Open `Admin -> First-run setup` and review the setup checklist.
6. Confirm `Admin -> Maintenance Health` shows no operational warnings.

## Alpha Notes

- This release is intended for evaluation and trusted small-group use.
- Back up the SQLite database before upgrades and periodically test restoration.
- Use an HTTPS reverse proxy and set `BINDERBRIDGE_PUBLIC_BASE_URL` for any internet-facing deployment.
- SMTP is optional; manual invite and password-recovery links remain available for local groups.
- Operators publishing modified versions should set `BINDERBRIDGE_SOURCE_URL` to the corresponding source repository.

## Validation

- Focused admin/setup tests: 48 tests
- Full automated test suite: 232 tests
- Clean Docker fresh-install smoke test covering first owner registration, setup wizard, public URL, registration policy, backup creation, manual invite link, CSV import, and setup completion banner
- Release smoke covering fresh install, schema upgrade, backup/restore integrity, complete trade workflow, and 5,000-row CSV import
- Published `v0.2.0-alpha.2` database upgrade smoke to `v0.2.0-alpha.3` with preserved collection data and SQLite integrity checks
- GitHub Actions PR checks for Python 3.10, 3.12, 3.13, and Docker startup smoke test

BinderBridge is licensed under the GNU Affero General Public License v3.0.
