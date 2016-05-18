from __future__ import print_function
import time, random
from twisted.python import log
from twisted.application import service, internet

SECONDS = 1.0
MINUTE = 60*SECONDS
HOUR = 60*MINUTE
DAY = 24*HOUR
MB = 1000*1000

CHANNEL_EXPIRATION_TIME = 3*DAY
EXPIRATION_CHECK_PERIOD = 2*HOUR

CLAIM = u"_claim"
RELEASE = u"_release"

def get_sides(row):
    return set([s for s in [row["side1"], row["side2"]] if s])
def make_sides(side1, side2):
    return list(sides) + [None] * (2 - len(sides))
def generate_mailbox_id():
    return base64.b32encode(os.urandom(8)).lower().strip("=")

# Unlike Channels, these instances are ephemeral, and are created and
# destroyed casually.
class Nameplate:
    def __init__(self, app_id, db, id, mailbox_id):
        self._app_id = app_id
        self._db = db
        self._id = id
        self._mailbox_id = mailbox_id

    def get_id(self):
        return self._id

    def get_mailbox_id(self):
        return self._mailbox_id

    def claim(self, side, when):
        db = self._db
        sides = get_sides(db.execute("SELECT `side1`, `side2` FROM `nameplates`"
                                     " WHERE `app_id`=? AND `id`=?",
                                     (self._app_id, self._id)).fetchone())
        old_sides = len(sides)
        sides.add(side)
        if len(sides) > 2:
            # XXX: crowded: bail
            pass
        sides12 = make_sides(sides)
        db.execute("UPDATE `nameplates` SET `side1`=?, `side2`=?"
                   " WHERE `app_id`=? AND `id`=?",
                   (sides12[0], sides12[1], self._app_id, self._id))
        if old_sides == 0:
            db.execute("UPDATE `mailboxes` SET `nameplate_started`=?"
                       " WHERE `app_id`=? AND `id`=?",
                       (when, self._app_id, self._mailbox_id))
        else:
            db.execute("UPDATE `mailboxes` SET `nameplate_second`=?"
                       " WHERE `app_id`=? AND `id`=?",
                       (when, self._app_id, self._mailbox_id))
        db.commit()

    def release(self, side, when):
        db = self._db
        sides = get_sides(db.execute("SELECT `side1`, `side2` FROM `nameplates`"
                                     " WHERE `app_id`=? AND `id`=?",
                                     (self._app_id, self._id)).fetchone())
        sides.discard(side)
        sides12 = make_sides(sides)
        db.execute("UPDATE `nameplates` SET `side1`=?, `side2`=?"
                   " WHERE `app_id`=? AND `id`=?",
                   (sides12[0], sides12[1], self._app_id, self._id))
        if len(sides) == 0:
            db.execute("UPDATE `mailboxes` SET `nameplate_closed`=?"
                       " WHERE `app_id`=? AND `id`=?",
                       (when, self._app_id, self._mailbox_id))
        db.commit()

class Mailbox:
    def __init__(self, app, db, blur_usage, log_requests, app_id, channelid):
        self._app = app
        self._db = db
        self._blur_usage = blur_usage
        self._log_requests = log_requests
        self._app_id = app_id
        self._channelid = channelid
        self._listeners = {} # handle -> (send_f, stop_f)
        # "handle" is a hashable object, for deregistration
        # send_f() takes a JSONable object, stop_f() has no args

    def get_channelid(self):
        return self._channelid

    def get_messages(self):
        messages = []
        db = self._db
        for row in db.execute("SELECT * FROM `messages`"
                              " WHERE `app_id`=? AND `channelid`=?"
                              " ORDER BY `server_rx` ASC",
                              (self._app_id, self._channelid)).fetchall():
            if row["phase"] in (CLAIM, RELEASE):
                continue
            messages.append({"phase": row["phase"], "body": row["body"],
                             "server_rx": row["server_rx"], "id": row["msgid"]})
        return messages

    def add_listener(self, handle, send_f, stop_f):
        self._listeners[handle] = (send_f, stop_f)
        return self.get_messages()

    def remove_listener(self, handle):
        self._listeners.pop(handle)

    def broadcast_message(self, phase, body, server_rx, msgid):
        for (send_f, stop_f) in self._listeners.values():
            send_f({"phase": phase, "body": body,
                    "server_rx": server_rx, "id": msgid})

    def _add_message(self, side, phase, body, server_rx, msgid):
        db = self._db
        db.execute("INSERT INTO `messages`"
                   " (`app_id`, `channelid`, `side`, `phase`,  `body`,"
                   "  `server_rx`, `msgid`)"
                   " VALUES (?,?,?,?,?, ?,?)",
                   (self._app_id, self._channelid, side, phase, body,
                    server_rx, msgid))
        db.commit()

    def claim(self, side):
        self._add_message(side, CLAIM, None, time.time(), None)

    def add_message(self, side, phase, body, server_rx, msgid):
        self._add_message(side, phase, body, server_rx, msgid)
        self.broadcast_message(phase, body, server_rx, msgid)
        return self.get_messages() # for rendezvous_web.py POST /add

    def release(self, side, mood):
        self._add_message(side, RELEASE, mood, time.time(), None)
        db = self._db
        seen = set([row["side"] for row in
                    db.execute("SELECT `side` FROM `messages`"
                               " WHERE `app_id`=? AND `channelid`=?",
                               (self._app_id, self._channelid))])
        freed = set([row["side"] for row in
                     db.execute("SELECT `side` FROM `messages`"
                                " WHERE `app_id`=? AND `channelid`=?"
                                " AND `phase`=?",
                                (self._app_id, self._channelid, RELEASE))])
        if seen - freed:
            return False
        self.delete_and_summarize()
        return True

    def is_idle(self):
        if self._listeners:
            return False
        c = self._db.execute("SELECT `server_rx` FROM `messages`"
                             " WHERE `app_id`=? AND `channelid`=?"
                             " ORDER BY `server_rx` DESC LIMIT 1",
                             (self._app_id, self._channelid))
        rows = c.fetchall()
        if not rows:
            return True
        old = time.time() - CHANNEL_EXPIRATION_TIME
        if rows[0]["server_rx"] < old:
            return True
        return False

    def _store_summary(self, summary):
        (started, result, total_time, waiting_time) = summary
        if self._blur_usage:
            started = self._blur_usage * (started // self._blur_usage)
        self._db.execute("INSERT INTO `usage`"
                         " (`type`, `started`, `result`,"
                         "  `total_time`, `waiting_time`)"
                         " VALUES (?,?,?, ?,?)",
                         (u"rendezvous", started, result,
                          total_time, waiting_time))
        self._db.commit()

    def _summarize(self, messages, delete_time):
        all_sides = set([m["side"] for m in messages])
        if len(all_sides) == 0:
            log.msg("_summarize was given zero messages") # shouldn't happen
            return

        started = min([m["server_rx"] for m in messages])
        # 'total_time' is how long the channel was occupied. That ends now,
        # both for channels that got pruned for inactivity, and for channels
        # that got pruned because of two RELEASE messages
        total_time = delete_time - started

        if len(all_sides) == 1:
            return (started, "lonely", total_time, None)
        if len(all_sides) > 2:
            # TODO: it'll be useful to have more detail here
            return (started, "crowded", total_time, None)

        # exactly two sides were involved
        A_side = sorted(messages, key=lambda m: m["server_rx"])[0]["side"]
        B_side = list(all_sides - set([A_side]))[0]

        # How long did the first side wait until the second side showed up?
        first_A = min([m["server_rx"] for m in messages if m["side"] == A_side])
        first_B = min([m["server_rx"] for m in messages if m["side"] == B_side])
        waiting_time = first_B - first_A

        # now, were all sides closed? If not, this is "pruney"
        A_deallocs = [m for m in messages
                      if m["phase"] == RELEASE and m["side"] == A_side]
        B_deallocs = [m for m in messages
                      if m["phase"] == RELEASE and m["side"] == B_side]
        if not A_deallocs or not B_deallocs:
            return (started, "pruney", total_time, None)

        # ok, both sides closed. figure out the mood
        A_mood = A_deallocs[0]["body"] # maybe None
        B_mood = B_deallocs[0]["body"] # maybe None
        mood = "quiet"
        if A_mood == u"happy" and B_mood == u"happy":
            mood = "happy"
        if A_mood == u"lonely" or B_mood == u"lonely":
            mood = "lonely"
        if A_mood == u"errory" or B_mood == u"errory":
            mood = "errory"
        if A_mood == u"scary" or B_mood == u"scary":
            mood = "scary"
        return (started, mood, total_time, waiting_time)

    def delete_and_summarize(self):
        db = self._db
        c = self._db.execute("SELECT * FROM `messages`"
                             " WHERE `app_id`=? AND `channelid`=?"
                             " ORDER BY `server_rx`",
                             (self._app_id, self._channelid))
        messages = c.fetchall()
        summary = self._summarize(messages, time.time())
        self._store_summary(summary)
        db.execute("DELETE FROM `messages`"
                   " WHERE `app_id`=? AND `channelid`=?",
                   (self._app_id, self._channelid))
        db.commit()

        # Shut down any listeners, just in case they're still lingering
        # around.
        for (send_f, stop_f) in self._listeners.values():
            stop_f()

        self._app.free_channel(self._channelid)

    def _shutdown(self):
        # used at test shutdown to accelerate client disconnects
        for (send_f, stop_f) in self._listeners.values():
            stop_f()

class AppNamespace:
    def __init__(self, db, welcome, blur_usage, log_requests, app_id):
        self._db = db
        self._welcome = welcome
        self._blur_usage = blur_usage
        self._log_requests = log_requests
        self._app_id = app_id
        self._channels = {}

    def get_nameplate_ids(self):
        db = self._db
        # TODO: filter this to numeric ids?
        c = db.execute("SELECT DISTINCT `id` FROM `nameplates`"
                       " WHERE `app_id`=?", (self._app_id,))
        return set([row["id"] for row in c.fetchall()])

    def find_available_nameplate_id(self):
        claimed = self.get_nameplate_ids()
        for size in range(1,4): # stick to 1-999 for now
            available = set()
            for id_int in range(10**(size-1), 10**size):
                id = u"%d" % id_int
                if id not in claimed:
                    available.add(id)
            if available:
                return random.choice(list(available))
        # ouch, 999 currently claimed. Try random ones for a while.
        for tries in range(1000):
            id_int = random.randrange(1000, 1000*1000)
            id = u"%d" % id_int
            if id not in claimed:
                return id
        raise ValueError("unable to find a free nameplate-id")

    def _get_mailbox_id(self, nameplate_id):
        row = self._db.execute("SELECT `mailbox_id` FROM `nameplates`"
                               " WHERE `app_id`=? AND `id`=?",
                               (self._app_id, nameplate_id)).fetchone()
        return row["mailbox_id"]

    def claim_nameplate(self, nameplate_id, side, when):
        assert isinstance(nameplate_id, type(u"")), type(nameplate_id)
        db = self._db
        rows = db.execute("SELECT * FROM `nameplates`"
                          " WHERE `app_id`=? AND `id`=?",
                          (self._app_id, nameplate_id))
        if rows:
            mailbox_id = rows[0]["mailbox_id"]
        else:
            if self._log_requests:
                log.msg("creating nameplate#%s for app_id %s" %
                        (nameplate_id, self._app_id))
            mailbox_id = UUID()
            db.execute("INSERT INTO `mailboxes`"
                       " (`app_id`, `id`)"
                       " VALUES(?,?)",
                       (self._app_id, mailbox_id))
            db.execute("INSERT INTO `nameplates`"
                       " (`app_id`, `id`, `mailbox_id`, `side1`, `side2`)"
                       " VALUES(?,?,?,?,?)",
                       (self._app_id, nameplate_id, mailbox_id, None, None))

        nameplate = Nameplate(self._app_id, self._db, nameplate_id, mailbox_id)
        nameplate.claim(side, when)
        return nameplate

    def claim_channel(self, channelid, side):
        assert isinstance(channelid, type(u"")), type(channelid)
        channel = self.get_channel(channelid)
        channel.claim(side)
        return channel

    def get_channel(self, channelid):
        assert isinstance(channelid, type(u""))
        if not channelid in self._channels:
            if self._log_requests:
                log.msg("spawning #%s for app_id %s" % (channelid, self._app_id))
            self._channels[channelid] = Channel(self, self._db,
                                                self._blur_usage,
                                                self._log_requests,
                                                self._app_id, channelid)
        return self._channels[channelid]

    def free_channel(self, channelid):
        # called from Channel.delete_and_summarize(), which deletes any
        # messages

        if channelid in self._channels:
            self._channels.pop(channelid)
        if self._log_requests:
            log.msg("freed+killed #%s, now have %d DB channels, %d live" %
                    (channelid, len(self.get_claimed()), len(self._channels)))

    def prune_old_channels(self):
        # For now, pruning is logged even if log_requests is False, to debug
        # the pruning process, and since pruning is triggered by a timer
        # instead of by user action. It does reveal which channels were
        # present when the pruning process began, though, so in the log run
        # it should do less logging.
        log.msg("  channel prune begins")
        # a channel is deleted when there are no listeners and there have
        # been no messages added in CHANNEL_EXPIRATION_TIME seconds
        channels = set(self.get_claimed()) # these have messages
        channels.update(self._channels) # these might have listeners
        for channelid in channels:
            log.msg("   channel prune checking %d" % channelid)
            channel = self.get_channel(channelid)
            if channel.is_idle():
                log.msg("   channel prune expiring %d" % channelid)
                channel.delete_and_summarize() # calls self.free_channel
        log.msg("  channel prune done, %r left" % (self._channels.keys(),))
        return bool(self._channels)

    def _shutdown(self):
        for channel in self._channels.values():
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
        assert isinstance(app_id, type(u""))
        if not app_id in self._apps:
            if self._log_requests:
                log.msg("spawning app_id %s" % (app_id,))
            self._apps[app_id] = AppNamespace(self._db, self._welcome,
                                             self._blur_usage,
                                             self._log_requests, app_id)
        return self._apps[app_id]

    def prune(self):
        # As with AppNamespace.prune_old_channels, we log for now.
        log.msg("beginning app prune")
        c = self._db.execute("SELECT DISTINCT `app_id` FROM `messages`")
        apps = set([row["app_id"] for row in c.fetchall()]) # these have messages
        apps.update(self._apps) # these might have listeners
        for app_id in apps:
            log.msg(" app prune checking %r" % (app_id,))
            still_active = self.get_app(app_id).prune_old_channels()
            if not still_active:
                log.msg("prune pops app %r" % (app_id,))
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
