from __future__ import print_function, unicode_literals
import os, time, random, base64
from collections import namedtuple
from twisted.python import log
from twisted.application import service, internet

SECONDS = 1.0
MINUTE = 60*SECONDS
HOUR = 60*MINUTE
DAY = 24*HOUR
MB = 1000*1000

CHANNEL_EXPIRATION_TIME = 2*HOUR
EXPIRATION_CHECK_PERIOD = 1*HOUR

def generate_mailbox_id():
    return base64.b32encode(os.urandom(8)).lower().strip(b"=").decode("ascii")

class CrowdedError(Exception):
    pass

Usage = namedtuple("Usage", ["started", "waiting_time", "total_time", "result"])
TransitUsage = namedtuple("TransitUsage",
                          ["started", "waiting_time", "total_time",
                           "total_bytes", "result"])

SidedMessage = namedtuple("SidedMessage", ["side", "phase", "body",
                                           "server_rx", "msg_id"])

class Mailbox:
    def __init__(self, app, db, app_id, mailbox_id):
        self._app = app
        self._db = db
        self._app_id = app_id
        self._mailbox_id = mailbox_id
        self._listeners = {} # handle -> (send_f, stop_f)
        # "handle" is a hashable object, for deregistration
        # send_f() takes a JSONable object, stop_f() has no args

    def open(self, side, when):
        # requires caller to db.commit()
        assert isinstance(side, type("")), type(side)
        db = self._db

        already = db.execute("SELECT * FROM `mailbox_sides`"
                             " WHERE `mailbox_id`=? AND `side`=?",
                             (self._mailbox_id, side)).fetchone()
        if not already:
            db.execute("INSERT INTO `mailbox_sides`"
                       " (`mailbox_id`, `opened`, `side`, `added`)"
                       " VALUES(?,?,?,?)",
                       (self._mailbox_id, True, side, when))
        db.execute("UPDATE `mailboxes` SET `updated`=? WHERE `id`=?",
                   (when, self._mailbox_id))
        db.commit() # XXX: reconcile the need for this with the comment above

    def get_messages(self):
        messages = []
        db = self._db
        for row in db.execute("SELECT * FROM `messages`"
                              " WHERE `app_id`=? AND `mailbox_id`=?"
                              " ORDER BY `server_rx` ASC",
                              (self._app_id, self._mailbox_id)).fetchall():
            sm = SidedMessage(side=row["side"], phase=row["phase"],
                              body=row["body"], server_rx=row["server_rx"],
                              msg_id=row["msg_id"])
            messages.append(sm)
        return messages

    def add_listener(self, handle, send_f, stop_f):
        # TODO: update 'updated'
        self._listeners[handle] = (send_f, stop_f)
        return self.get_messages()

    def remove_listener(self, handle):
        self._listeners.pop(handle)

    def has_listeners(self):
        return bool(self._listeners)

    def broadcast_message(self, sm):
        for (send_f, stop_f) in self._listeners.values():
            send_f(sm)

    def _add_message(self, sm):
        self._db.execute("INSERT INTO `messages`"
                         " (`app_id`, `mailbox_id`, `side`, `phase`,  `body`,"
                         "  `server_rx`, `msg_id`)"
                         " VALUES (?,?,?,?,?, ?,?)",
                         (self._app_id, self._mailbox_id, sm.side,
                          sm.phase, sm.body, sm.server_rx, sm.msg_id))
        self._db.execute("UPDATE `mailboxes` SET `updated`=? WHERE `id`=?",
                         (sm.server_rx, self._mailbox_id))
        self._db.commit()

    def add_message(self, sm):
        assert isinstance(sm, SidedMessage)
        self._add_message(sm)
        self.broadcast_message(sm)

    def close(self, side, mood, when):
        assert isinstance(side, type("")), type(side)
        db = self._db
        row = db.execute("SELECT * FROM `mailboxes`"
                         " WHERE `app_id`=? AND `id`=?",
                         (self._app_id, self._mailbox_id)).fetchone()
        if not row:
            return

        row = db.execute("SELECT * FROM `mailbox_sides`"
                         " WHERE `mailbox_id`=? AND `side`=?",
                         (self._mailbox_id, side)).fetchone()
        if not row:
            return
        db.execute("UPDATE `mailbox_sides` SET `opened`=?, `mood`=?"
                   " WHERE `mailbox_id`=? AND `side`=?",
                   (False, mood, self._mailbox_id, side))
        db.commit()

        # are any sides still open?
        side_rows = db.execute("SELECT * FROM `mailbox_sides`"
                               " WHERE `mailbox_id`=?",
                               (self._mailbox_id,)).fetchall()
        if any([sr["opened"] for sr in side_rows]):
            return

        # nope. delete and summarize
        db.execute("DELETE FROM `messages` WHERE `mailbox_id`=?",
                   (self._mailbox_id,))
        db.execute("DELETE FROM `mailbox_sides` WHERE `mailbox_id`=?",
                   (self._mailbox_id,))
        db.execute("DELETE FROM `mailboxes` WHERE `id`=?", (self._mailbox_id,))
        self._app._summarize_mailbox_and_store(side_rows, when, pruned=False)
        db.commit()
        # Shut down any listeners, just in case they're still lingering
        # around.
        for (send_f, stop_f) in self._listeners.values():
            stop_f()
        self._app.free_mailbox(self._mailbox_id)

    def is_active(self):
        return bool(self._listeners)

    def _shutdown(self):
        # used at test shutdown to accelerate client disconnects
        for (send_f, stop_f) in self._listeners.values():
            stop_f()

class AppNamespace:
    def __init__(self, db, blur_usage, log_requests, app_id):
        self._db = db
        self._blur_usage = blur_usage
        self._log_requests = log_requests
        self._app_id = app_id
        self._mailboxes = {}

    def is_active(self):
        # An idle AppNamespace does not need to be kept in memory: it can be
        # reconstructed from the DB if needed. And active one must be kept
        # alive.
        for mb in self._mailboxes.values():
            if mb.is_active():
                return True
        return False

    def get_nameplate_ids(self):
        db = self._db
        # TODO: filter this to numeric ids?
        c = db.execute("SELECT DISTINCT `name` FROM `nameplates`"
                       " WHERE `app_id`=?", (self._app_id,))
        return set([row["name"] for row in c.fetchall()])

    def _find_available_nameplate_id(self):
        claimed = self.get_nameplate_ids()
        for size in range(1,4): # stick to 1-999 for now
            available = set()
            for id_int in range(10**(size-1), 10**size):
                id = "%d" % id_int
                if id not in claimed:
                    available.add(id)
            if available:
                return random.choice(list(available))
        # ouch, 999 currently claimed. Try random ones for a while.
        for tries in range(1000):
            id_int = random.randrange(1000, 1000*1000)
            id = "%d" % id_int
            if id not in claimed:
                return id
        raise ValueError("unable to find a free nameplate-id")

    def allocate_nameplate(self, side, when):
        nameplate_id = self._find_available_nameplate_id()
        mailbox_id = self.claim_nameplate(nameplate_id, side, when)
        del mailbox_id # ignored, they'll learn it from claim()
        return nameplate_id

    def claim_nameplate(self, name, side, when):
        # when we're done:
        # * there will be one row for the nameplate
        #  * there will be one 'side' attached to it, with claimed=True
        # * a mailbox id and mailbox row will be created
        #  * a mailbox 'side' will be attached, with opened=True
        assert isinstance(name, type("")), type(name)
        assert isinstance(side, type("")), type(side)
        db = self._db
        row = db.execute("SELECT * FROM `nameplates`"
                         " WHERE `app_id`=? AND `name`=?",
                         (self._app_id, name)).fetchone()
        if not row:
            if self._log_requests:
                log.msg("creating nameplate#%s for app_id %s" %
                        (name, self._app_id))
            mailbox_id = generate_mailbox_id()
            self._add_mailbox(mailbox_id, side, when) # ensure row exists
            sql = ("INSERT INTO `nameplates`"
                   " (`app_id`, `name`, `mailbox_id`, `updated`)"
                   " VALUES(?,?,?,?)")
            npid = db.execute(sql, (self._app_id, name, mailbox_id, when)
                              ).lastrowid
        else:
            npid = row["id"]
            mailbox_id = row["mailbox_id"]

        row = db.execute("SELECT * FROM `nameplate_sides`"
                         " WHERE `nameplates_id`=? AND `side`=?",
                         (npid, side)).fetchone()
        if not row:
            db.execute("INSERT INTO `nameplate_sides`"
                       " (`nameplates_id`, `claimed`, `side`, `added`)"
                       " VALUES(?,?,?,?)",
                       (npid, True, side, when))
        db.execute("UPDATE `nameplates` SET `updated`=? WHERE `id`=?",
                   (when, npid))
        db.commit()

        self.open_mailbox(mailbox_id, side, when) # may raise CrowdedError
        rows = db.execute("SELECT * FROM `nameplate_sides`"
                          " WHERE `nameplates_id`=?", (npid,)).fetchall()
        if len(rows) > 2:
            # this line will probably never get hit: any crowding is noticed
            # on mailbox_sides first, inside open_mailbox()
            raise CrowdedError("too many sides have claimed this nameplate")
        return mailbox_id

    def release_nameplate(self, name, side, when):
        # when we're done:
        # * the 'claimed' flag will be cleared on the nameplate_sides row
        # * if the nameplate is now unused (no claimed sides):
        #  * mailbox.nameplate_closed will be populated
        #  * the nameplate row will be removed
        #  * the nameplate sides will be removed
        assert isinstance(name, type("")), type(name)
        assert isinstance(side, type("")), type(side)
        db = self._db
        np_row = db.execute("SELECT * FROM `nameplates`"
                            " WHERE `app_id`=? AND `name`=?",
                            (self._app_id, name)).fetchone()
        if not np_row:
            return
        npid = np_row["id"]
        row = db.execute("SELECT * FROM `nameplate_sides`"
                         " WHERE `nameplates_id`=? AND `side`=?",
                         (npid, side)).fetchone()
        if not row:
            return
        db.execute("UPDATE `nameplate_sides` SET `claimed`=?"
                   " WHERE `nameplates_id`=? AND `side`=?",
                   (False, npid, side))
        db.commit()

        # now, are there any remaining claims?
        side_rows = db.execute("SELECT * FROM `nameplate_sides`"
                               " WHERE `nameplates_id`=?",
                               (npid,)).fetchall()
        claims = [1 for sr in side_rows if sr["claimed"]]
        if claims:
            return
        # delete and summarize
        db.execute("DELETE FROM `nameplate_sides` WHERE `nameplates_id`=?",
                   (npid,))
        db.execute("DELETE FROM `nameplates` WHERE `id`=?", (npid,))
        self._summarize_nameplate_and_store(side_rows, when, pruned=False)
        db.commit()

    def _summarize_nameplate_and_store(self, side_rows, delete_time, pruned):
        # requires caller to db.commit()
        u = self._summarize_nameplate_usage(side_rows, delete_time, pruned)
        self._db.execute("INSERT INTO `nameplate_usage`"
                         " (`app_id`,"
                         " `started`, `total_time`, `waiting_time`, `result`)"
                         " VALUES (?, ?,?,?,?)",
                         (self._app_id,
                          u.started, u.total_time, u.waiting_time, u.result))

    def _summarize_nameplate_usage(self, side_rows, delete_time, pruned):
        times = sorted([row["added"] for row in side_rows])
        started = times[0]
        if self._blur_usage:
            started = self._blur_usage * (started // self._blur_usage)
        waiting_time = None
        if len(times) > 1:
            waiting_time = times[1] - times[0]
        total_time = delete_time - times[0]
        result = "lonely"
        if len(times) == 2:
            result = "happy"
        if pruned:
            result = "pruney"
        if len(times) > 2:
            result = "crowded"
        return Usage(started=started, waiting_time=waiting_time,
                     total_time=total_time, result=result)

    def _add_mailbox(self, mailbox_id, side, when):
        assert isinstance(mailbox_id, type("")), type(mailbox_id)
        db = self._db
        row = db.execute("SELECT * FROM `mailboxes`"
                         " WHERE `app_id`=? AND `id`=?",
                         (self._app_id, mailbox_id)).fetchone()
        if not row:
            self._db.execute("INSERT INTO `mailboxes`"
                             " (`app_id`, `id`, `updated`)"
                             " VALUES(?,?,?)",
                             (self._app_id, mailbox_id, when))
            # we don't need a commit here, because mailbox.open() only
            # does SELECT FROM `mailbox_sides`, not from `mailboxes`

    def open_mailbox(self, mailbox_id, side, when):
        assert isinstance(mailbox_id, type("")), type(mailbox_id)
        self._add_mailbox(mailbox_id, side, when) # ensure row exists
        db = self._db
        if not mailbox_id in self._mailboxes: # ensure Mailbox object exists
            if self._log_requests:
                log.msg("spawning #%s for app_id %s" % (mailbox_id,
                                                        self._app_id))
            self._mailboxes[mailbox_id] = Mailbox(self, self._db,
                                                  self._app_id, mailbox_id)
        mailbox = self._mailboxes[mailbox_id]

        # delegate to mailbox.open() to add a row to mailbox_sides, and
        # update the mailbox.updated timestamp
        mailbox.open(side, when)
        db.commit()
        rows = db.execute("SELECT * FROM `mailbox_sides`"
                          " WHERE `mailbox_id`=?",
                          (mailbox_id,)).fetchall()
        if len(rows) > 2:
            raise CrowdedError("too many sides have opened this mailbox")
        return mailbox

    def free_mailbox(self, mailbox_id):
        # called from Mailbox.delete_and_summarize(), which deletes any
        # messages

        if mailbox_id in self._mailboxes:
            self._mailboxes.pop(mailbox_id)
        #if self._log_requests:
        #    log.msg("freed+killed #%s, now have %d DB mailboxes, %d live" %
        #            (mailbox_id, len(self.get_claimed()), len(self._mailboxes)))

    def _summarize_mailbox_and_store(self, side_rows, delete_time, pruned):
        db = self._db
        u = self._summarize_mailbox(side_rows, delete_time, pruned)
        db.execute("INSERT INTO `mailbox_usage`"
                   " (`app_id`,"
                   "  `started`, `total_time`, `waiting_time`, `result`)"
                   " VALUES (?, ?,?,?,?)",
                   (self._app_id,
                    u.started, u.total_time, u.waiting_time, u.result))

    def _summarize_mailbox(self, side_rows, delete_time, pruned):
        times = sorted([row["added"] for row in side_rows])
        started = times[0]
        if self._blur_usage:
            started = self._blur_usage * (started // self._blur_usage)
        waiting_time = None
        if len(times) > 1:
            waiting_time = times[1] - times[0]
        total_time = delete_time - times[0]

        num_sides = len(times)
        if num_sides == 0:
            result = "quiet"
        elif num_sides == 1:
            result = "lonely"
        else:
            result = "happy"

        # "mood" is only recorded at close()
        moods = [row["mood"] for row in side_rows if row.get("mood")]
        if "lonely" in moods:
            result = "lonely"
        if "errory" in moods:
            result = "errory"
        if "scary" in moods:
            result = "scary"
        if pruned:
            result = "pruney"
        if num_sides > 2:
            result = "crowded"

        return Usage(started=started, waiting_time=waiting_time,
                     total_time=total_time, result=result)

    def prune(self, now, old):
        # For now, pruning is logged even if log_requests is False, to debug
        # the pruning process, and since pruning is triggered by a timer
        # instead of by user action. It does reveal which mailboxes were
        # present when the pruning process began, though, so in the log run
        # it should do less logging.
        log.msg(" prune begins (%s)" % self._app_id)
        db = self._db
        modified = False
        # for all `mailboxes`: classify as new or old
        OLD = 0; NEW = 1
        all_mailboxes = {}
        for row in db.execute("SELECT * FROM `mailboxes` WHERE `app_id`=?",
                              (self._app_id,)).fetchall():
            mailbox_id = row["id"]
            if row["updated"] > old:
                which = NEW
            else:
                which = OLD
            all_mailboxes[mailbox_id] = which
        #log.msg(" 2: all_mailboxes", all_mailboxes)

        # for all mailbox objects with active listeners:
        #   classify the mailbox as new
        for mailbox_id in self._mailboxes:
            #log.msg(" -5: checking", mailbox_id, self._mailboxes[mailbox_id])
            if self._mailboxes[mailbox_id].has_listeners():
                all_mailboxes[mailbox_id] = NEW
        #log.msg(" 5: all_mailboxes", all_mailboxes)

        # for all `nameplates`:
        #   classify as new or old
        #   if the linked mailbox exists:
        #     if it is new:
        #       classify nameplate as new
        #     if it is old:
        #       if the nameplate is new:
        #         classify mailbox as new
        old_nameplate_ids = []
        for row in db.execute("SELECT * FROM `nameplates` WHERE `app_id`=?",
                              (self._app_id,)).fetchall():
            npid = row["id"]
            if row["updated"] > old:
                which = NEW
            else:
                which = OLD
            mailbox_id = row["mailbox_id"]
            if mailbox_id in all_mailboxes:
                if all_mailboxes[mailbox_id] == NEW:
                    which = NEW
                else:
                    if which == NEW:
                        all_mailboxes[mailbox_id] = NEW
            if which == OLD:
                old_nameplate_ids.append(npid)
        #log.msg(" 6: old_nameplate_ids", old_nameplate_ids)

        # delete all old nameplates
        #   invariant check: if there is a linked mailbox, it is old

        for npid in old_nameplate_ids:
            log.msg("  deleting nameplate", npid)
            side_rows = db.execute("SELECT * FROM `nameplate_sides`"
                                   " WHERE `nameplates_id`=?",
                                   (npid,)).fetchall()
            db.execute("DELETE FROM `nameplate_sides` WHERE `nameplates_id`=?",
                       (npid,))
            db.execute("DELETE FROM `nameplates` WHERE `id`=?", (npid,))
            self._summarize_nameplate_and_store(side_rows, now, pruned=True)
            modified = True

        # delete all messages for old mailboxes
        # delete all old mailboxes

        for mailbox_id, which in all_mailboxes.items():
            if which == OLD:
                log.msg("  deleting mailbox", mailbox_id)
                side_rows = db.execute("SELECT * FROM `mailbox_sides`"
                                       " WHERE `mailbox_id`=?",
                                       (mailbox_id,)).fetchall()
                db.execute("DELETE FROM `messages` WHERE `mailbox_id`=?",
                           (mailbox_id,))
                db.execute("DELETE FROM `mailbox_sides` WHERE `mailbox_id`=?",
                           (mailbox_id,))
                db.execute("DELETE FROM `mailboxes` WHERE `id`=?",
                           (mailbox_id,))
                self._summarize_mailbox_and_store(side_rows, now, pruned=True)
                modified = True

        if modified:
            db.commit()
        log.msg("  prune complete, modified:", modified)

    def _shutdown(self):
        for channel in self._mailboxes.values():
            channel._shutdown()

class Rendezvous(service.MultiService):
    def __init__(self, db, welcome, blur_usage):
        service.MultiService.__init__(self)
        self._db = db
        self._welcome = welcome
        self._blur_usage = blur_usage
        log_requests = blur_usage is None
        self._log_requests = log_requests
        self._apps = {}
        t = internet.TimerService(EXPIRATION_CHECK_PERIOD, self.prune)
        t.setServiceParent(self)

    def get_welcome(self):
        return self._welcome
    def get_log_requests(self):
        return self._log_requests

    def get_app(self, app_id):
        assert isinstance(app_id, type(""))
        if not app_id in self._apps:
            if self._log_requests:
                log.msg("spawning app_id %s" % (app_id,))
            self._apps[app_id] = AppNamespace(self._db,
                                              self._blur_usage,
                                              self._log_requests, app_id)
        return self._apps[app_id]

    def get_all_apps(self):
        apps = set()
        for row in self._db.execute("SELECT DISTINCT `app_id`"
                                    " FROM `nameplates`").fetchall():
            apps.add(row["app_id"])
        for row in self._db.execute("SELECT DISTINCT `app_id`"
                                    " FROM `mailboxes`").fetchall():
            apps.add(row["app_id"])
        for row in self._db.execute("SELECT DISTINCT `app_id`"
                                    " FROM `messages`").fetchall():
            apps.add(row["app_id"])
        return apps

    def prune(self, now=None, old=None):
        # As with AppNamespace.prune_old_mailboxes, we log for now.
        log.msg("beginning app prune")
        now = now or time.time()
        old = old or (now - CHANNEL_EXPIRATION_TIME)
        for app_id in sorted(self.get_all_apps()):
            log.msg(" app prune checking %r" % (app_id,))
            app = self.get_app(app_id)
            app.prune(now, old)
            if not app.is_active(): # meaning no websockets
                log.msg(" pruning idle app", app_id)
                self._apps.pop(app_id)
        log.msg("app prune ends, %d remaining apps" % len(self._apps))

    def stopService(self):
        # This forcibly boots any clients that are still connected, which
        # helps with unit tests that use threads for both clients. One client
        # hits an exception, which terminates the test (and .tearDown calls
        # stopService on the relay), but the other client (in its thread) is
        # still waiting for a message. By killing off all connections, that
        # other client gets an error, and exits promptly.
        for app in self._apps.values():
            app._shutdown()
        return service.MultiService.stopService(self)
