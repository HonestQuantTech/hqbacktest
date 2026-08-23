"""Entry point so `python -m hqbacktest run --config ...` works.

Delegates to `hqbacktest.cli.__main__:main` and re-raises SystemExit so
argparse error codes propagate.
"""

import sys

from hqbacktest.cli.__main__ import main


if __name__ == "__main__":
    sys.exit(main())
