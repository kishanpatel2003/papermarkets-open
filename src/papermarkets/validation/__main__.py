"""Allow running as `python -m papermarkets.validation`."""

import sys

from papermarkets.validation.validate import main

sys.exit(main())
