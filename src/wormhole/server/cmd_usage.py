from __future__ import print_function, unicode_literals
import os, time, json
from collections import defaultdict
import click
from humanize import naturalsize
from .database import get_db

def abbrev(t):
    if t is None:
        return "-"
    if t > 1.0:
        return "%.3fs" % t
    if t > 1e-3:
        return "%.1fms" % (t*1e3)
    return "%.1fus" % (t*1e6)


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
           naturalsize(total_bytes),
           time.ctime(started),
          ))

def show_usage(args):
    print("closed for renovation")
    return 0
    if not os.path.exists("relay.sqlite"):
        raise click.UsageError(
            "cannot find relay.sqlite, please run from the server directory"
        )
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
        if row["type"] == "rendezvous":
            counters = rendezvous_counters
        elif row["type"] == "transit":
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
        print(" %s total bytes, %sps" % (naturalsize(total_transit_bytes),
                                         naturalsize(rate)))
        print("", ", ".join(["%s=%d (%d%%)" %
                             (k, counters[k], (100.0 * counters[k] / total))
                             for k in sorted(counters)
                             if k != "total"]))
    return 0

def tail_usage(args):
    if not os.path.exists("relay.sqlite"):
        raise click.UsageError(
            "cannot find relay.sqlite, please run from the server directory"
        )
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

def count_channels(args):
    if not os.path.exists("relay.sqlite"):
        raise click.UsageError(
            "cannot find relay.sqlite, please run from the server directory"
        )
    db = get_db("relay.sqlite")
    c_list = []
    c_dict = {}
    def add(key, value):
        c_list.append((key, value))
        c_dict[key] = value
    OLD = time.time() - 10*60
    def q(query, values=()):
        return list(db.execute(query, values).fetchone().values())[0]
    add("apps", q("SELECT COUNT(DISTINCT(`app_id`)) FROM `nameplates`"))

    add("total nameplates", q("SELECT COUNT() FROM `nameplates`"))
    add("waiting nameplates", q("SELECT COUNT() FROM `nameplates`"
                                " WHERE `second` is null"))
    add("connected nameplates", q("SELECT COUNT() FROM `nameplates`"
                                  " WHERE `second` is not null"))
    add("stale nameplates", q("SELECT COUNT() FROM `nameplates`"
                              " where `updated` < ?", (OLD,)))

    add("total mailboxes", q("SELECT COUNT() FROM `mailboxes`"))
    add("waiting mailboxes", q("SELECT COUNT() FROM `mailboxes`"
                                " WHERE `second` is null"))
    add("connected mailboxes", q("SELECT COUNT() FROM `mailboxes`"
                                 " WHERE `second` is not null"))

    stale_mailboxes = 0
    for mbox_row in db.execute("SELECT * FROM `mailboxes`").fetchall():
        newest = db.execute("SELECT `server_rx` FROM `messages`"
                            " WHERE `app_id`=? AND `mailbox_id`=?"
                            " ORDER BY `server_rx` DESC LIMIT 1",
                            (mbox_row["app_id"], mbox_row["id"])).fetchone()
        if newest and newest[0] < OLD:
            stale_mailboxes += 1
    add("stale mailboxes", stale_mailboxes)

    add("messages", q("SELECT COUNT() FROM `messages`"))

    if args.json:
        print(json.dumps(c_dict))
    else:
        for (key, value) in c_list:
            print(key, value)
    return 0

def count_events(args):
    if not os.path.exists("relay.sqlite"):
        raise click.UsageError(
            "cannot find relay.sqlite, please run from the server directory"
        )
    db = get_db("relay.sqlite")
    c_list = []
    c_dict = {}
    def add(key, value):
        c_list.append((key, value))
        c_dict[key] = value
    def q(query, values=()):
        return list(db.execute(query, values).fetchone().values())[0]

    add("apps", q("SELECT COUNT(DISTINCT(`app_id`)) FROM `nameplate_usage`"))

    add("total nameplates", q("SELECT COUNT() FROM `nameplate_usage`"))
    add("happy nameplates", q("SELECT COUNT() FROM `nameplate_usage`"
                              " WHERE `result`='happy'"))
    add("lonely nameplates", q("SELECT COUNT() FROM `nameplate_usage`"
                               " WHERE `result`='lonely'"))
    add("pruney nameplates", q("SELECT COUNT() FROM `nameplate_usage`"
                               " WHERE `result`='pruney'"))
    add("crowded nameplates", q("SELECT COUNT() FROM `nameplate_usage`"
                                " WHERE `result`='crowded'"))

    add("total mailboxes", q("SELECT COUNT() FROM `mailbox_usage`"))
    add("happy mailboxes", q("SELECT COUNT() FROM `mailbox_usage`"
                             " WHERE `result`='happy'"))
    add("scary mailboxes", q("SELECT COUNT() FROM `mailbox_usage`"
                             " WHERE `result`='scary'"))
    add("lonely mailboxes", q("SELECT COUNT() FROM `mailbox_usage`"
                              " WHERE `result`='lonely'"))
    add("errory mailboxes", q("SELECT COUNT() FROM `mailbox_usage`"
                              " WHERE `result`='errory'"))
    add("pruney mailboxes", q("SELECT COUNT() FROM `mailbox_usage`"
                              " WHERE `result`='pruney'"))
    add("crowded mailboxes", q("SELECT COUNT() FROM `mailbox_usage`"
                               " WHERE `result`='crowded'"))

    add("total transit", q("SELECT COUNT() FROM `transit_usage`"))
    add("happy transit", q("SELECT COUNT() FROM `transit_usage`"
                           " WHERE `result`='happy'"))
    add("lonely transit", q("SELECT COUNT() FROM `transit_usage`"
                            " WHERE `result`='lonely'"))
    add("errory transit", q("SELECT COUNT() FROM `transit_usage`"
                            " WHERE `result`='errory'"))

    add("transit bytes", q("SELECT SUM(`total_bytes`) FROM `transit_usage`"))

    if args.json:
        print(json.dumps(c_dict))
    else:
        for (key, value) in c_list:
            print(key, value)
    return 0
