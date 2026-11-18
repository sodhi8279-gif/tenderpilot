"""The TenderPilot version. One place: the installer (packaging/tenderpilot.iss, version_info.txt), the in-app
updater and the published GitHub Release all use this number (tests/test_updater.py checks they agree).
Raise it for every release that should be offered as an update, and add a section to CHANGELOG.md."""
APP_VERSION = "1.5.0"
