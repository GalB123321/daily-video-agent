#!/bin/bash
# Installer for the daily video agent launchd job.
#
# This script is portable across machines and users. It figures out where the
# repo lives from its own location, finds a usable python, and GENERATES the
# launchd plist with correct absolute paths for this machine. launchd requires
# absolute paths, so the plist is written fresh here rather than copied.
#
# What this does:
#   1. Resolves the repo root from this script location.
#   2. Picks python: the repo .venv if present, else system python3.
#   3. Writes ~/Library/LaunchAgents/com.user.dailyvideo.plist for this machine.
#   4. Unloads any previous copy, then loads the fresh one.
#
# Notes to keep in mind:
#   · The Mac must be awake at 19:00 for the job to fire. launchd will run a
#     missed job soon after wake, but a Mac that is shut down will simply skip it.
#   · If the watch folder is a synced location (iCloud, Dropbox, Google Drive),
#     the runner may need Full Disk Access. Grant it in System Settings,
#     Privacy and Security, Full Disk Access, for the python3 binary or Terminal.
#   · To change the hour, edit RUN_HOUR below, then run this script again.

set -e

RUN_HOUR=19
RUN_MINUTE=0
LABEL="com.user.dailyvideo"
PLIST_NAME="${LABEL}.plist"

# Repo root = parent of the directory this script lives in, fully resolved.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Pick python: prefer the project venv, then a venv aware python3, then system.
if [ -x "${BASE}/.venv/bin/python3" ]; then
  PYTHON="${BASE}/.venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  PYTHON="/usr/bin/python3"
fi

# PATH for the minimal launchd environment. Covers Apple silicon and Intel brew.
LAUNCHD_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

DEST_DIR="${HOME}/Library/LaunchAgents"
DEST_PLIST="${DEST_DIR}/${PLIST_NAME}"

echo "Installing ${PLIST_NAME} ..."
echo "  repo:   ${BASE}"
echo "  python: ${PYTHON}"

mkdir -p "${DEST_DIR}"
mkdir -p "${BASE}/logs"

cat > "${DEST_PLIST}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
        <string>${BASE}/run.py</string>
    </array>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>${RUN_HOUR}</integer>
        <key>Minute</key>
        <integer>${RUN_MINUTE}</integer>
    </dict>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>${LAUNCHD_PATH}</string>
    </dict>

    <key>StandardOutPath</key>
    <string>${BASE}/logs/out.log</string>

    <key>StandardErrorPath</key>
    <string>${BASE}/logs/err.log</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
PLIST

echo "Wrote plist to ${DEST_PLIST}"

# Unload any existing copy first. Ignore errors on a first time install.
launchctl unload "${DEST_PLIST}" 2>/dev/null || true

# Load the fresh copy.
launchctl load "${DEST_PLIST}"
echo "Loaded job into launchd."

echo ""
echo "Next steps:"
echo "  1. Confirm the job is registered:  launchctl list | grep ${LABEL}"
echo "  2. Trigger a manual test run now:   launchctl start ${LABEL}"
echo "  3. Watch the logs:                  tail -f ${BASE}/logs/out.log"
echo "  4. Errors are written to:           ${BASE}/logs/err.log"
echo ""
echo "Reminder: the Mac must be awake at ${RUN_HOUR}:00 for the daily run to fire."
echo "If the watch folder is a synced location, grant Full Disk Access to python3."
