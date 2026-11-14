"""
almf.__main__: Entry point for running ALMF as a module (`python -m almf ...`).
"""

import sys
from almf.cli import main

if __name__ == "__main__":
    sys.exit(main())
