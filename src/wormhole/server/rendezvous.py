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

ALLOCATE = u"_allocate"
DEALLOCATE = u"_deallocate"

class Channel:
    def __init__(self, app, db, welcome, blur_usage, log_requests,
                 appid, channelid):
        self._app = app
        self._db = db
        self._blur_usage = blur_usage
        self._log_requests = log_requests
        self._appid = appid
        self._channelid = channelid
        self._listeners = set() # instances with .send_rendezvous_event (that
                                # takes a JSONable object) and
                                # .stop_rendezvous_watcher()

    def get_channelid(self):
        return self._channelid

    def get_messages(self):
        messages = []
        db = self._db
        for row in db.execute("SELECT * FROM `messages`"
                              " WHERE `appid`=? AND `channelid`=?"
                              " ORDER BY `server_rx` ASC",
                              (self._appid, self._channelid)).fetchall():
            if row["phase"] in (u"_allocate", u"_deallocate"):
                continue
            messages.append({"phase": row["phase"], "body": row["body"],
                             "server_rx": row["server_rx"], "id": row["msgid"]})
        return messages

    def add_listener(self, ep):
        self._listeners.add(ep)
        return self.get_messages()

    def remove_listener(self, ep):
        self._listeners.discard(ep)

    def broadcast_message(self, phase, body, server_rx, msgid):
        for ep in self._listeners:
            ep.send_rendezvous_event({"phase": phase, "body": body,
                                      "server_rx": server_rx, "id": msgid})

    def _add_message(self, side, phase, body, server_rx, msgid):
        db = self._db
        db.execute("INSERT INTO `messages`"
                   " (`appid`, `channelid`, `side`, `phase`,  `body`,"
                   "  `server_rx`, `msgid`)"
                   " VALUES (?,?,?,?,?, ?,?)",
                   (self._appid, self._channelid, side, phase, body,
                    server_rx, msgid))
        db.commit()

    def allocate(self, side):
        self._add_message(side, ALLOCATE, None, time.time(), None)

    def add_message(self, side, phase, body, server_rx, msgid):
        self._add_message(side, phase, body, server_rx, msgid)
        self.broadcast_message(phase, body, server_rx, msgid)
        return self.get_messages() # for rendezvous_web.py POST /add

    def deallocate(self, side, mood):
        self._add_message(side, DEALLOCATE, mood, time.time(), None)
        db = self._db
        seen = set([row["side"] for row in
                    db.execute("SELECT `side` FROM `messages`"
                               " WHERE `appid`=? AND `channelid`=?",
                               (self._appid, self._channelid))])
        freed = set([row["side"] for row in
                     db.execute("SELECT `side` FROM `messages`"
                                " WHERE `appid`=? AND `channelid`=?"
                                " AND `phase`=?",
                                (self._appid, self._channelid, DEALLOCATE))])
        if seen - freed:
            return False
        self.delete_and_summarize()
        return True

    def is_idle(self):
        if self._listeners:
            return False
        c = self._db.execute("SELECT `server_rx` FROM `messages`"
                             " WHERE `appid`=? AND `channelid`=?"
                             " ORDER BY `server_rx` DESC LIMIT 1",
                             (self._appid, self._channelid))
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
        # that got pruned because of two DEALLOCATE messages
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
                      if m["phase"] == DEALLOCATE and m["side"] == A_side]
        B_deallocs = [m for m in messages
                      if m["phase"] == DEALLOCATE and m["side"] == B_side]
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
                             " WHERE `appid`=? AND `channelid`=?"
                             " ORDER BY `server_rx`",
                             (self._appid, self._channelid))
        messages = c.fetchall()
        summary = self._summarize(messages, time.time())
        self._store_summary(summary)
        db.execute("DELETE FROM `messages`"
                   " WHERE `appid`=? AND `channelid`=?",
                   (self._appid, self._channelid))
        db.commit()

        # Shut down any listeners, just in case they're still lingering
        # around.
        for ep in self._listeners:
            ep.stop_rendezvous_watcher()

        self._app.free_channel(self._channelid)

    def _shutdown(self):
        # used at test shutdown to accelerate client disconnects
        for ep in self._listeners:
            ep.stop_rendezvous_watcher()

class AppNamespace:
    def __init__(self, db, welcome, blur_usage, log_requests, appid):
        self._db = db
        self._welcome = welcome
        self._blur_usage = blur_usage
        self._log_requests = log_requests
        self._appid = appid
        self._channels = {}

    def get_allocated(self):
        db = self._db
        c = db.execute("SELECT DISTINCT `channelid` FROM `messages`"
                       " WHERE `appid`=?", (self._appid,))
        return set([row["channelid"] for row in c.fetchall()])

    def find_available_channelid(self):
        allocated = self.get_allocated()
        for size in range(1,4): # stick to 1-999 for now
            available = set()
            for cid in range(10**(size-1), 10**size):
                if cid not in allocated:
                    available.add(cid)
            if available:
                return random.choice(list(available))
        # ouch, 999 currently allocated. Try random ones for a while.
        for tries in range(1000):
            cid = random.randrange(1000, 1000*1000)
            if cid not in allocated:
                return cid
        raise ValueError("unable to find a free channel-id")

    def allocate_channel(self, channelid, side):
        channel = self.get_channel(channelid)
        channel.allocate(side)
        return channel

    def get_channel(self, channelid):
        assert isinstance(channelid, int)
        if not channelid in self._channels:
            if self._log_requests:
                log.msg("spawning #%d for appid %s" % (channelid, self._appid))
            self._channels[channelid] = Channel(self, self._db, self._welcome,
                                                self._blur_usage,
                                                self._log_requests,
                                                self._appid, channelid)
        return self._channels[channelid]

    def free_channel(self, channelid):
        # called from Channel.delete_and_summarize(), which deletes any
        # messages

        if channelid in self._channels:
            self._channels.pop(channelid)
        if self._log_requests:
            log.msg("freed+killed #%d, now have %d DB channels, %d live" %
                    (channelid, len(self.get_allocated()), len(self._channels)))

    def prune_old_channels(self):
        # For now, pruning is logged even if log_requests is False, to debug
        # the pruning process, and since pruning is triggered by a timer
        # instead of by user action. It does reveal which channels were
        # present when the pruning process began, though, so in the log run
        # it should do less logging.
        log.msg("  channel prune begins")
        # a channel is deleted when there are no listeners and there have
        # been no messages added in CHANNEL_EXPIRATION_TIME seconds
        channels = set(self.get_allocated()) # these have messages
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

    def get_app(self, appid):
        assert isinstance(appid, type(u""))
        if not appid in self._apps:
            if self._log_requests:
                log.msg("spawning appid %s" % (appid,))
            self._apps[appid] = AppNamespace(self._db, self._welcome,
                                             self._blur_usage,
                                             self._log_requests, appid)
        return self._apps[appid]

    def prune(self):
        # As with AppNamespace.prune_old_channels, we log for now.
        log.msg("beginning app prune")
        c = self._db.execute("SELECT DISTINCT `appid` FROM `messages`")
        apps = set([row["appid"] for row in c.fetchall()]) # these have messages
        apps.update(self._apps) # these might have listeners
        for appid in apps:
            log.msg(" app prune checking %r" % (appid,))
            still_active = self.get_app(appid).prune_old_channels()
            if not still_active:
                log.msg("prune pops app %r" % (appid,))
                self._apps.pop(appid)
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
