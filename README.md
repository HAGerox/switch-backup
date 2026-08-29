# Switch Backup

Switch Backup is a simple macOS app for saving Cisco switch configurations.
Add your login details and switches, start a backup, and the app creates a ZIP
file in your Downloads folder containing the startup configuration from each
switch.

Everything runs directly from your Mac. Switch details stay on your computer,
passwords are protected by macOS Keychain, and configurations are not uploaded
to an online service.

[Download the latest release](https://github.com/HAGerox/switch-backup/releases/latest)

## Highlights

- Add a single switch or a range of IPv4 addresses
- Automatically discover switch names and models
- Save multiple sets of login details
- Remember which login worked for each switch
- Back up several switches at once with clear progress and results
- Save selected running configurations to startup configuration
- Create one easy-to-store ZIP file for every backup
- Keep individual configurations as clearly named text files

## Getting started

1. Download the latest DMG from the link above.
2. Open it and drag **Switch Backup** into your Applications folder.
3. Open the app and add at least one login under **Credentials**.
4. Add your switches under **Switches**. The app will connect to them and fill
   in their names and models where possible.
5. Select the switches you want and start the backup.

The finished ZIP is saved in your Downloads folder with a name such as
`Switch Backups - 2026-08-29 16-30-00.zip`. Inside it, each switch has a separate
text file such as `201 - Core Switch.txt`.

The current release is not notarized by Apple. If macOS blocks it the first
time, Control-click the app, choose **Open**, and confirm that you want to open
it.

## Privacy and safety

- Passwords are stored in macOS Keychain rather than in the app's database.
- The app connects directly from your Mac to your switches over SSH.
- Backups only read the startup configuration. The separate **Save to Startup**
  action asks for confirmation before using the switch driver's configuration-save
  operation to replace startup configuration with the current running configuration.
- Backup ZIP files contain plain text configurations and are not encrypted, so
  store and share them carefully.

## Compatibility

Switch Backup is built for Apple Silicon and Intel Macs. It currently supports
Cisco switches reached over SSH on port 22 using IPv4 and a username and
password.

Telnet, IPv6, SSH keys, scheduled backups, configuration history, comparisons,
and configuration editing are not currently supported.

## For developers

The app uses Python, Toga, Briefcase, and Netmiko. Python 3.10 or newer is
required for local development.

Run the app in development mode:

```bash
./run-dev-macos.sh
```

Run the tests and build a macOS DMG:

```bash
python3 -m pip install "pytest>=8,<9"
python3 -m pytest -q
./build-macos.sh
```

## License

Copyright © 2026 Finn Stanley. Switch Backup is available under the
[MIT License](LICENSE).
