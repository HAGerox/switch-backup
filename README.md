# Switch Backup

A deliberately small macOS utility for backing up Cisco switch running configurations.

[Download the latest release](https://github.com/HAGerox/switch-backup/releases/latest)

## What it does

- Two simple tabs: **Credentials** and **Switches**.
- Stores credential metadata in a tiny local SQLite database.
- Stores passwords in **macOS Keychain** using Python `keyring`.
- Allows multiple credentials. A switch's last successful credential is tried first on later runs; otherwise credentials are tried in the order shown.
- Automatically authenticates and discovers the hostname, platform driver, and model when switches are added.
- Lets users edit a switch name or IP address by double-clicking its row.
- Adds either one switch or an inclusive range using a focused popup with
  separate first and last IP address fields.
- Uses Cisco-specific Netmiko drivers to authenticate and identify supported switch families without entering configuration mode.
- Uses Netmiko's platform-aware session preparation and reads the complete running
  configuration (`show running-config detailed` on Catalyst 1200/1300-class switches).
- Backs up up to three switches concurrently and shows per-switch progress.
- Creates one ZIP in `~/Downloads` and does **not** retain a backup database.
- Config files inside the ZIP are plain `.txt`, named like:

  `201 - Core Switch.txt`

  where `201` is the last octet of the switch IP and `Core Switch` is the entered name or the hostname discovered from the switch prompt.

## Multiple credential behaviour

Unimus' documented discovery mode tries all configured credentials against a device and remembers whichever works. This app follows the same overall idea, but intentionally uses a deterministic order rather than random order:

1. If this switch has previously backed up successfully, try that credential first.
2. Then try the other credentials in the order they were added.
3. Once a credential works, remember it for the next backup.

This minimizes unnecessary failed authentication attempts on normal day-to-day use. Be careful about loading many incorrect passwords for the same username if your AAA policy has account lockout enabled.

## Scope / intentional limitations of v0.1

- **SSH only**, TCP port 22. No Telnet in v0.1.
- Cisco-focused. Netmiko is multi-vendor, but the fallback drivers are deliberately Cisco switch families.
- IPv4 only.
- Username/password authentication only. SSH keys can be added later.
- No schedules, config history, diffing, startup-config saving, or configuration push.
- No host-key pinning UI yet; Netmiko is currently configured for compatibility-first SSH host-key handling.
- Discovery only reads `show version` and `show inventory`. Backup only reads the
  running configuration; it does not enter configuration mode or write anything to
  switches.

## Run it on a Mac in development mode

You need Python 3.10+ installed. Homebrew Python is fine.

From Terminal in this project folder:

```bash
./run-dev-macos.sh
```

The script creates a local `.venv`, installs Briefcase 0.4.4, and starts the app in Briefcase development mode.

## Build a normal macOS app/DMG for your own Mac

```bash
./build-macos.sh
```

This builds the macOS application with Briefcase and packages it with an ad-hoc signature. The resulting artifact will be under `dist/`.

The packaged app includes its own Python runtime and Python dependencies, so the
Mac running it does not need Python, Netmiko, or any other package installed.

For distribution to other Macs without Gatekeeper friction, use an Apple Developer ID so Briefcase can sign and notarize the app rather than using `--adhoc-sign`.

## Continuous integration and releases

Pull requests and pushes to `main` run the test suite on macOS. Pushing a semantic
version tag such as `v0.1.0` builds a universal macOS DMG and publishes it to a GitHub
Release.

The workflow currently uses ad-hoc signing because no Apple Developer credentials are
stored in the repository. Before distributing broadly, configure Developer ID signing
and notarization in GitHub Actions so Gatekeeper accepts the downloaded application.

Source and releases: <https://github.com/HAGerox/switch-backup>

## Project layout

```text
src/switchbackup/
  app.py          minimal Toga GUI
  backup.py       concurrency + ZIP creation
  network.py      Netmiko discovery and Cisco backup
  storage.py      SQLite + Keychain persistence
  ip_utils.py     single/range/CIDR parsing
  filenames.py    requested TXT naming
  models.py       small data models

tests/
  unit tests for ranges, naming, storage, credential order and ZIP output
```

## Why this architecture

- **Netmiko** handles the CLI-specific details (prompt detection, terminal setup, paging and Cisco-family drivers) instead of reimplementing SSH screen-scraping.
- **Toga** provides a native macOS Cocoa UI through `toga-cocoa`.
- **Briefcase** produces a real `.app`/DMG rather than a Java JAR or background web service.
- **keyring** maps to macOS Keychain for passwords.
- **SQLite** persists only the tiny list of devices/credential metadata and the last successful discovery information.
