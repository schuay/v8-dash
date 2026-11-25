# Copyright 2026 The v8-dash developers
# SPDX-License-Identifier: MIT

"""Package entry for `python3 -m shepherd.dash`."""

import sys

from .server import main

sys.exit(main())
