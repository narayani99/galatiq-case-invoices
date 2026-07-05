"""Wrapper main.py at the project root for backwards compatibility.

Enables both styles of execution:
1. Original README style:
   python main.py --invoice_path=data/invoices/invoice_1001.txt

2. Subcommand style:
   python main.py process-batch --invoice_dir=data/invoices
   python main.py init-db
"""

import sys
from pathlib import Path

# Add project root to sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.main import main

if __name__ == "__main__":
    # If invoked directly with --invoice_path and no subcommand,
    # insert the "process" subcommand automatically.
    if len(sys.argv) > 1 and any(arg.startswith("--invoice_path") for arg in sys.argv):
        # Only do this if a subcommand isn't already specified
        commands = {"process", "process-batch", "resume", "init-db"}
        if not any(arg in commands for arg in sys.argv):
            sys.argv.insert(1, "process")

    sys.exit(main())
