#!/usr/bin/env bash
# v1.0.1

# fleasion by @cro.p
# distributed in https://discord.gg/hXyhKehEZF
# https://github.com/CroppingFlea479/Fleasion
# run.sh made by @jan_darkk

set -euo pipefail

REQUIRED_PYTHON_VERSION='Python 3.12'

__install_python() {
  #NOTE: I purposefully didn't add `--noconfirm`/`--yes`, not
  # a fan of stuff installing without confirmation on Linux.
  if command -v apt &>/dev/null; then
    # Works on Ubuntu 24.04 LTS
    sudo apt install python3.12 python3-requests
  elif command -v dnf &>/dev/null; then
    # Works on Fedora 39
    sudo dnf install python3 python3-requests
  elif command -v pacman &>/dev/null; then
    # Confirmed to work on CachyOS and will work on Arch Linux.
    sudo pacman -S python python-requests --needed
  else
    echo "Distro not supported. Please install $REQUIRED_PYTHON_VERSION and python-requests."
  fi
  clear
}

check_python() {
  #NOTE: pip doesnt work on all distros (`externally-managed-environment` error), so importing `requests` is done.
  if (command -v python && python -c 'import requests') &>/dev/null; then
    pyver=$(python --version)
    pyver="${pyver:: -2}" # removes python subversion, "Python 3.12.5" -> "Python 3.12"

    if [[ $pyver != "$REQUIRED_PYTHON_VERSION" ]]; then
      echo "Python is version \`$pyver\`, need \`$REQUIRED_PYTHON_VERSION\`. Attempting automatic installation."
      __install_python
    fi
  else
    # shellcheck disable=SC2016
    echo 'Python or python-requests is not installed. Attempting automatic installation'
    __install_python
  fi
  echo "Your python installation meets the requirements of $REQUIRED_PYTHON_VERSION."
  clear
}

check_fleasion() {
  # "Just in case, check if Fleasion is there."
  fleasion_link="https://raw.githubusercontent.com/CroppingFlea479/Fleasion/main"
  if [[ ! -f fleasion.py ]]; then
    # shellcheck disable=SC2016
    echo '`fleasion.py` not found. Downloading,,,'
    curl -fLO "$fleasion_link/fleasion.py"
  fi
  clear
}

launch_fleasion() {
  python3.12 fleasion.py
}

main() {
  clear
  check_python
  check_fleasion
  launch_fleasion
}

main "$@"
