# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""Single place for the version string and the product name."""
__version__ = "0.11.1-wip"
NAME = "Twoo STEP CAD Toolbox"
# pip insists on PEP 440 numbers, which have no "-wip"; this is the same version spelled the way pip likes it.
PEP440 = __version__.replace("-wip", ".dev0")
