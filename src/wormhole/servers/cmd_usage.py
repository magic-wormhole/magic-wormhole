from __future__ import print_function
import os, time
from collections import defaultdict
from ..database import get_db
from ..errors import UsageError

def abbrev(t):
    if t is None:
        return "-"
    if t > 1.0:
        return "%.3fs" % t
    if t > 1e-3:
        return "%.1fms" % (t*1e3)
    return "%.1fus" % (t*1e6)

def print_event(event):
    started, result, waiting_time, total_time = event
    followthrough = None
    if waiting_time and total_time:
        followthrough = total_time - waiting_time
    print("%s: %-6s total=%7s wait=%7s ft=%7s" %
          (time.ctime(started), result,
           abbrev(total_time),
           abbrev(waiting_time),
           abbrev(followthrough),
          ))

def show_usage(args):
    if not os.path.exists("relay.sqlite"):
        raise UsageError("cannot find relay.sqlite, please run from the server directory")
    oldest = None
    newest = None
    counters = defaultdict(int)
    db = get_db("relay.sqlite")
    c = db.execute("SELECT * FROM `usage` ORDER BY `started` ASC LIMIT ?",
                   (args.n,))
    for row in c.fetchall():
        counters["total"] += 1
        counters[row["result"]] += 1
        if oldest is None or row["started"] < oldest:
            oldest = row["started"]
        if newest is None or row["started"] > newest:
            newest = row["started"]
        event = (row["started"], row["result"],
                 row["waiting_time"], row["total_time"])
        print_event(event)
    total = counters["total"]
    if total:
        print("(most recent started %s ago)" % abbrev(time.time() - newest))
        elapsed = time.time() - oldest
        print("%d events in %s (%.2f per hour)" % (total, abbrev(elapsed),
                                                   (3600 * total / elapsed)))
        print(", ".join(["%s=%d (%d%%)" %
                         (k, counters[k], (100.0 * counters[k] / total))
                         for k in sorted(counters)
                         if k != "total"]))
    return 0

def tail_usage(args):
    if not os.path.exists("relay.sqlite"):
        raise UsageError("cannot find relay.sqlite, please run from the server directory")
    db = get_db("relay.sqlite")
    # we don't seem to have unique row IDs, so this is an inaccurate and
    # inefficient hack
    seen = set()
    while True:
        old = time.time() - 2*60*60
        c = db.execute("SELECT * FROM `usage` WHERE `started` > ?"
                       " ORDER BY `started` ASC", (old,))
        for row in c.fetchall():
            event = (row["started"], row["result"],
                     row["waiting_time"], row["total_time"])
            if event not in seen:
                print_event(event)
                seen.add(event)
        time.sleep(2)
    return 0
