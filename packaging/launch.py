"""PyInstaller entry script for the app (see packaging/build.sh)."""

import sys

from chronon.gui.__main__ import main

sys.exit(main())
