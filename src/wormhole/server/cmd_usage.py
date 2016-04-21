from __future__ import print_function
import os, time
from collections import defaultdict
from .database import get_db
from ..errors import UsageError

def abbrev(t):
    if t is None:
        return "-"
    if t > 1.0:
        return "%.3fs" % t
    if t > 1e-3:
        return "%.1fms" % (t*1e3)
    return "%.1fus" % (t*1e6)

def abbreviate_space(s, SI=True):
    if s is None:
        return "-"
    if SI:
        U = 1000.0
        isuffix = "B"
    else:
        U = 1024.0
        isuffix = "iB"
    def r(count, suffix):
        return "%.2f %s%s" % (count, suffix, isuffix)

    if s < 1024: # 1000-1023 get emitted as bytes, even in SI mode
        return "%d B" % s
    if s < U*U:
        return r(s/U, "k")
    if s < U*U*U:
        return r(s/(U*U), "M")
    if s < U*U*U*U:
        return r(s/(U*U*U), "G")
    if s < U*U*U*U*U:
        return r(s/(U*U*U*U), "T")
    if s < U*U*U*U*U*U:
        return r(s/(U*U*U*U*U), "P")
    return r(s/(U*U*U*U*U*U), "E")

def print_event(event):
    event_type, started, result, total_bytes, waiting_time, total_time = event
    followthrough = None
    if waiting_time and total_time:
        followthrough = total_time - waiting_time
    print("%17s: total=%7s wait=%7s ft=%7s size=%s (%s)" %
          ("%s-%s" % (event_type, result),
           abbrev(total_time),
           abbrev(waiting_time),
           abbrev(followthrough),
           abbreviate_space(total_bytes),
           time.ctime(started),
          ))

def show_usage(args):
    if not os.path.exists("relay.sqlite"):
        raise UsageError("cannot find relay.sqlite, please run from the server directory")
    oldest = None
    newest = None
    rendezvous_counters = defaultdict(int)
    transit_counters = defaultdict(int)
    total_transit_bytes = 0
    db = get_db("relay.sqlite")
    c = db.execute("SELECT * FROM `usage`"
                   " ORDER BY `started` ASC LIMIT ?",
                   (args.n,))
    for row in c.fetchall():
        if row["type"] == u"rendezvous":
            counters = rendezvous_counters
        elif row["type"] == u"transit":
            counters = transit_counters
            total_transit_bytes += row["total_bytes"]
        else:
            continue
        counters["total"] += 1
        counters[row["result"]] += 1
        if oldest is None or row["started"] < oldest:
            oldest = row["started"]
        if newest is None or row["started"] > newest:
            newest = row["started"]
        event = (row["type"], row["started"], row["result"],
                 row["total_bytes"], row["waiting_time"], row["total_time"])
        print_event(event)
    if rendezvous_counters["total"] or transit_counters["total"]:
        print("---")
        print("(most recent started %s ago)" % abbrev(time.time() - newest))
    if rendezvous_counters["total"]:
        print("rendezvous events:")
        counters = rendezvous_counters
        elapsed = time.time() - oldest
        total = counters["total"]
        print(" %d events in %s (%.2f per hour)" % (total, abbrev(elapsed),
                                                    (3600 * total / elapsed)))
        print("", ", ".join(["%s=%d (%d%%)" %
                             (k, counters[k], (100.0 * counters[k] / total))
                             for k in sorted(counters)
                             if k != "total"]))
    if transit_counters["total"]:
        print("transit events:")
        counters = transit_counters
        elapsed = time.time() - oldest
        total = counters["total"]
        print(" %d events in %s (%.2f per hour)" % (total, abbrev(elapsed),
                                                    (3600 * total / elapsed)))
        rate = total_transit_bytes / elapsed
        print(" %s total bytes, %sps" % (abbreviate_space(total_transit_bytes),
                                         abbreviate_space(rate)))
        print("", ", ".join(["%s=%d (%d%%)" %
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
    try:
        while True:
            old = time.time() - 2*60*60
            c = db.execute("SELECT * FROM `usage`"
                           " WHERE `started` > ?"
                           " ORDER BY `started` ASC", (old,))
            for row in c.fetchall():
                event = (row["type"], row["started"], row["result"],
                         row["total_bytes"], row["waiting_time"],
                         row["total_time"])
                if event not in seen:
                    print_event(event)
                    seen.add(event)
            time.sleep(2)
    except KeyboardInterrupt:
        return 0
    return 0
