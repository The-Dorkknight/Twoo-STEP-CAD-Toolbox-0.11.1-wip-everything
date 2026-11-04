# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
params — turn what a front end sends (strings from a web form, numbers from a command line) into the
typed values a plugin's manifest declares.

The rules, the same for every plugin:
  * a missing or blank value is the manifest's default (or None for a nullable parameter);
  * a number that is not a number is an error that names the parameter;
  * a choice that is not one of the choices is the default (the page and the command line send blanks
    and old values; the automatic setting is always a safe answer);
  * keys the manifest does not declare are passed through untouched (a job carries extras such as the
    output folder, a picture's path or a model's defines).
"""
from __future__ import annotations


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def coerce_one(p, v):
    if _blank(v):
        return None if p.nullable else p.default
    try:
        if p.type == "float":
            x = float(v)
            if x != x or x in (float("inf"), float("-inf")):
                raise ValueError
            return x
        if p.type == "int":
            return int(float(v))
        if p.type == "bool":
            if isinstance(v, str):
                return v.strip().lower() in ("1", "true", "yes", "on")
            return bool(v)
        if p.type == "choice":
            return str(v) if str(v) in p.choices else p.default
        return str(v)
    except (TypeError, ValueError):
        raise ValueError(f"{p.name} must be {'a whole number' if p.type == 'int' else 'a number'}, got {v!r}")


def coerce(manifest, raw):
    """All the manifest's parameters, typed, plus any extra keys the caller passed."""
    raw = dict(raw or {})
    out = dict(raw)
    for p in manifest.params:
        out[p.name] = coerce_one(p, raw.get(p.name))
    return out
