# BinderBridge Release Checklist

Use this checklist after feature work is frozen and before creating a release tag.

## Prepare

- Confirm the working tree contains only intended release changes.
- Update `binderbridge/version.py`, `CHANGELOG.md`, `RELEASE_NOTES.md`, README examples, and example configuration.
- Create and verify a backup of any important installation used for manual testing.

## Automated Validation

```powershell
python -m compileall -q app.py binderbridge tests scripts
python -m unittest discover -s tests
python scripts/release_smoke.py
python scripts/release_upgrade_smoke.py v0.1.0-alpha.1
python scripts/browser_smoke.py
```

- Confirm fresh initialization reaches the current schema.
- Confirm the published-release upgrade preserves the sentinel collection row.
- Confirm backup restore, SQLite quick check, and foreign-key check pass.
- Confirm the complete trade smoke workflow reaches `completed`.
- Confirm the 5,000-row import completes.
- Confirm the Playwright browser smoke covers first-run setup, import preview/apply, collection search, wishlist matching, browse-to-trade proposal, recipient notifications, and desktop/mobile layout screenshots for tabbed workspaces.

## Manual Smoke

- Sign in and confirm Account, Admin, Collection, Wishlist, Browse, Trades, Notifications, and Groups render.
- Create or revoke an invite and verify the manual link fallback.
- Exercise password recovery with the configured delivery mode.
- Preview, apply, and undo a small collection import.
- Create, accept, and complete a trade between two test users.
- Confirm trade and import notifications appear.
- Review Admin health for setup warnings, failed jobs, backup status, and database status.
- Check one desktop and one narrow mobile viewport.

## Android Client

- Use the separate `BinderBridge-Android` repository for Android release work.
- Build a debug APK with `./gradlew :app:assembleDebug` from the Android repo.
- Connect the debug app to a local server with `http://10.0.2.2:8000` on the emulator.
- Build the signed local-release APK with `./gradlew :app:assembleLocalRelease` from the Android repo when local HTTP device testing is needed.
- Build a release APK with `./gradlew :app:assembleRelease` from the Android repo.
- Confirm the release APK is signed with the intended keystore and verifies with `apksigner`.
- Confirm the release app rejects `http://` BinderBridge URLs and connects to an `https://` origin.
- Smoke collection browsing, quick add, card lookup, trade alerts, and notification detail with a least-privilege test token, then revoke the token.
- Confirm signing keys, keystores, and local signing properties were not added to the repository.
- Confirm the release keystore and passwords are backed up somewhere secure.

## Publish

- Push a release branch and wait for every GitHub Actions check to pass.
- Merge the release pull request.
- Create the annotated release tag from the merge commit.
- Publish the GitHub release using `RELEASE_NOTES.md`.
- Verify the tag checkout and Docker image start successfully.

## After Release

- Confirm the release page, changelog links, and installation instructions point to the new tag.
- Upgrade a disposable copy of an existing installation and review Admin health.
- Keep the previous release tag available for rollback.
