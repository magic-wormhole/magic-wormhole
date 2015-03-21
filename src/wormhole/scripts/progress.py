from __future__ import print_function
import sys, time

def print_progress(completed, expected):
    # scp does "<<FILENAME >>(13%  168MB  39.3MB/s   00:27 ETA)"
    # we do "Progress: ####         13%  168MB"
    fmt = "Progress: %-40s %3d%%  %4d%s"
    short_unit_size, short_unit_name = 1, "B"
    if expected > 9999:
        short_unit_size, short_unit_name = 1000, "KB"
    if expected > 9999*1000:
        short_unit_size, short_unit_name = 1000*1000, "MB"
    if expected > 9999*1000*1000:
        short_unit_size, short_unit_name = 1000*1000*1000, "GB"

    percentage_complete = (1.0 * completed / expected) if expected else 1.0
    bars = "#" * int(percentage_complete * 40)
    perc = int(100 * percentage_complete)
    short_unit_count = int(completed / short_unit_size)
    out = fmt % (bars, perc, short_unit_count, short_unit_name)
    print("\r"+" "*70, end="")
    print("\r"+out, end="")
    sys.stdout.flush()

def start_progress(expected, UPDATE_EVERY=0.2):
    print_progress(0, expected)
    next_update = time.time() + UPDATE_EVERY
    return next_update

def update_progress(next_update, completed, expected, UPDATE_EVERY=0.2):
    now = time.time()
    if now < next_update:
        return next_update
    next_update = now + UPDATE_EVERY
    print_progress(completed, expected)
    return next_update

def finish_progress(expected):
    print_progress(expected, expected)
    print()
