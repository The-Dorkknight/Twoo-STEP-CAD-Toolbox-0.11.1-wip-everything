# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""The example plug-in's whole job: `run(job)` writes job.out and returns a report."""
from dataclasses import dataclass


@dataclass
class Report:
    ok: bool
    lines: int

    def summary(self):                      # shown in the web page and printed by the command line
        return f"{self.lines} line(s) counted"


def run(job):
    with open(job.src, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    if job.params["skip_blank"]:            # parameters arrive typed, as the manifest declares them
        lines = [x for x in lines if x.strip()]
    job.log(f"Read {len(lines)} line(s)")
    job.progress(0.5)
    with open(job.out, "w", encoding="utf-8") as f:
        f.write(f"{len(lines)}\n")
    job.progress(1.0)
    return Report(ok=True, lines=len(lines))
