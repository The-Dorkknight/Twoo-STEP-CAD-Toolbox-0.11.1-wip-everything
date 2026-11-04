# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Optional command-line hooks. Without this file `tostep hello in.txt [out]` still works and takes the
manifest's parameters from nowhere; with it, command-line options become parameters."""

CLI_HELP = "Count the lines of a text file."


def add_arguments(ap):
    ap.add_argument("--skip-blank", action="store_true", help="do not count empty lines")


def params_from_args(a, output):
    return {"skip_blank": a.skip_blank}
