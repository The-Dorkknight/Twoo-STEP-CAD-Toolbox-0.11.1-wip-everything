# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Start script used by the launchers: python run_tostep.py [command] [options].
Running a script puts its own folder first on the module path, which is how `tostep` is found
without installing anything (the launchers run Python with -E -s, which ignore PYTHONPATH)."""
import sys

from tostep.frontends.cli import main

sys.exit(main())
