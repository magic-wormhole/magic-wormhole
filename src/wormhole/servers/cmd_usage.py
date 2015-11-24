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

def show_usage(args):
    if not os.path.exists("relay.sqlite"):
        raise UsageError("cannot find relay.sqlite, please run from the server directory")
    if args.follow:
        raise UsageError("--follow not yet implemented")
    oldest_event = None
    newest_event = None
    counters = defaultdict(int)
    db = get_db("relay.sqlite")
    c = db.execute("SELECT * FROM `usage` ORDER BY `started` ASC LIMIT ?", (args.n,))
    for row in c.fetchall():
        counters["total"] += 1
        counters[row["result"]] += 1
        if oldest_event is None or row["started"] < oldest_event:
            oldest_event = row["started"]
        if newest_event is None or row["started"] > newest_event:
            newest_event = row["started"]
        followthrough = None
        if row["waiting_time"] and row["total_time"]:
            followthrough = row["total_time"] - row["waiting_time"]
        #print(dir(row))
        print("%s: %-6s total=%7s wait=%7s ft=%7s" %
              (time.ctime(row["started"]), row["result"],
               abbrev(row["waiting_time"]),
               abbrev(row["total_time"]),
               abbrev(followthrough),
              ))
    total = counters["total"]
    if total:
        print("(most recent started %s ago)" % abbrev(time.time() - newest_event))
        elapsed = time.time() - oldest_event
        print("%d events in %s (%.2f per hour)" % (total, abbrev(elapsed),
                                                   (3600 * total / elapsed)))
        print(", ".join(["%s=%d (%s%%)" % (k, counters[k], (100.0 * counters[k] / total))
                         for k in sorted(counters)
                         if k != "total"]))
    return 0
