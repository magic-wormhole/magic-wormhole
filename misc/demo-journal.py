import os, sys, json, contextlib, random
from twisted.internet import task, defer, endpoints
from twisted.application import service, internet
from twisted.web import server, static, resource
from wormhole import journal, wormhole

# considerations for state management:
# * be somewhat principled about the data (e.g. have a schema)
# * discourage accidental schema changes
# * avoid surprise mutations by app code (don't hand out mutables)
# * discourage app from keeping state itself: make state object easy enough
#   to use for everything. App should only hold objects that are active
#   (Services, subscribers, etc). App must wire up these objects each time.


def parse(args):
    raise NotImplementedError


def update_my_state():
    raise NotImplementedError


class State(object):
    @classmethod
    def create_empty(klass):
        self = klass()
        # to avoid being tripped up by state-mutation side-effect bugs, we
        # hold the serialized state in RAM, and re-deserialize it each time
        # someone asks for a piece of it.    # iid->invitation_stat
        empty = {"version": 1,
                 "invitations": {},
                 "contacts": [],
                 }
        self._bytes = json.dumps(empty).encode("utf-8")
        return self

    @classmethod
    def from_filename(klass, fn):
        self = klass()
        with open(fn, "rb") as f:
            bytes = f.read()
        self._bytes = bytes
        # version check
        data = self._as_data()
        assert data["version"] == 1
        # schema check?
        return self

    def save_to_filename(self, fn):
        tmpfn = fn + ".tmp"
        with open(tmpfn, "wb") as f:
            f.write(self._bytes)
        os.rename(tmpfn, fn)

    def _as_data(self):
        return json.loads(bytes.decode("utf-8"))

    @contextlib.contextmanager
    def _mutate(self):
        data = self._as_data()
        yield data  # mutable
        self._bytes = json.dumps(data).encode("utf-8")

    def get_all_invitations(self):
        return self._as_data()["invitations"]

    def add_invitation(self, iid, invitation_state):
        with self._mutate() as data:
            data["invitations"][iid] = invitation_state

    def update_invitation(self, iid, invitation_state):
        with self._mutate() as data:
            assert iid in data["invitations"]
            data["invitations"][iid] = invitation_state

    def remove_invitation(self, iid):
        with self._mutate() as data:
            del data["invitations"][iid]

    def add_contact(self, contact):
        with self._mutate() as data:
            data["contacts"].append(contact)


class Root(resource.Resource):
    pass


class Status(resource.Resource):
    def __init__(self, c):
        resource.Resource.__init__(self)
        self._call = c

    def render_GET(self, req):
        data = self._call()
        req.setHeader(b"content-type", "text/plain")
        return data


class Action(resource.Resource):
    def __init__(self, c):
        resource.Resource.__init__(self)
        self._call = c

    def render_POST(self, req):
        req.setHeader(b"content-type", "text/plain")
        try:
            args = json.load(req.content)
        except ValueError:
            req.setResponseCode(500)
            return b"bad JSON"
        data = self._call(args)
        return data


class Agent(service.MultiService):
    def __init__(self, basedir, reactor):
        service.MultiService.__init__(self)
        self._basedir = basedir
        self._reactor = reactor

        root = Root()
        site = server.Site(root)
        ep = endpoints.serverFromString(reactor, "tcp:8220")
        internet.StreamServerEndpointService(ep, site).setServiceParent(self)

        self._jm = journal.JournalManager(self._save_state)

        root.putChild(b"", static.Data("root", "text/plain"))
        root.putChild(b"list-invitations", Status(self._list_invitations))
        root.putChild(b"invite", Action(self._invite))  # {petname:}
        root.putChild(b"accept", Action(self._accept))  # {petname:, code:}

        self._state_fn = os.path.join(self._basedir, "state.json")
        self._state = State.from_filename(self._state_fn)

        self._wormholes = {}
        for iid, invitation_state in self._state.get_all_invitations().items():
            def _dispatch(event, *args, **kwargs):
                self._dispatch_wormhole_event(iid, event, *args, **kwargs)
            w = wormhole.journaled_from_data(invitation_state["wormhole"],
                                             reactor=self._reactor,
                                             journal=self._jm,
                                             event_handler=self,
                                             event_handler_args=(iid,))
            self._wormholes[iid] = w
            w.setServiceParent(self)

    def _save_state(self):
        self._state.save_to_filename(self._state_fn)

    def _list_invitations(self):
        inv = self._state.get_all_invitations()
        lines = ["%d: %s" % (iid, inv[iid]) for iid in sorted(inv)]
        return b"\n".join(lines) + b"\n"

    def _invite(self, args):
        print("invite", args)
        petname = args["petname"]
        # it'd be better to use a unique object for the event_handler
        # correlation, but we can't store them into the state database. I'm
        # not 100% sure we need one for the database: maybe it should hold a
        # list instead, and assign lookup keys at runtime. If they really
        # need to be serializable, they should be allocated rather than
        # random.
        iid = random.randint(1, 1000)
        my_pubkey = random.randint(1, 1000)
        with self._jm.process():
            w = wormhole.journaled(reactor=self._reactor, journal=self._jm,
                                   event_handler=self,
                                   event_handler_args=(iid,))
            self._wormholes[iid] = w
            w.setServiceParent(self)
            w.get_code()  # event_handler means code returns via callback
            invitation_state = {"wormhole": w.to_data(),
                                "petname": petname,
                                "my_pubkey": my_pubkey,
                                }
            self._state.add_invitation(iid, invitation_state)
        return b"ok"

    def _accept(self, args):
        print("accept", args)
        petname = args["petname"]
        code = args["code"]
        iid = random.randint(1, 1000)
        my_pubkey = random.randint(2, 2000)
        with self._jm.process():
            w = wormhole.journaled(reactor=self._reactor, journal=self._jm,
                                   event_dispatcher=self,
                                   event_dispatcher_args=(iid,))
            w.set_code(code)
            md = {"my_pubkey": my_pubkey}
            w.send(json.dumps(md).encode("utf-8"))
            invitation_state = {"wormhole": w.to_data(),
                                "petname": petname,
                                "my_pubkey": my_pubkey,
                                }
            self._state.add_invitation(iid, invitation_state)
        return b"ok"

    # dispatch options:
    # * register one function, which takes (eventname, *args)
    #   * to handle multiple wormholes, app must give is a closure
    # * register multiple functions (one per event type)
    # * register an object, with well-known method names
    # * extra: register args and/or kwargs with the callback
    #
    # events to dispatch:
    #  generated_code(code)
    #  got_verifier(verifier_bytes)
    #  verified()
    #  got_data(data_bytes)
    #  closed()

    def wormhole_dispatch_got_code(self, code, iid):
        # we're already in a jm.process() context
        invitation_state = self._state.get_all_invitations()[iid]
        invitation_state["code"] = code
        self._state.update_invitation(iid, invitation_state)
        self._wormholes[iid].set_code(code)
        # notify UI subscribers to update the display

    def wormhole_dispatch_got_verifier(self, verifier, iid):
        pass

    def wormhole_dispatch_verified(self, _, iid):
        pass

    def wormhole_dispatch_got_data(self, data, iid):
        invitation_state = self._state.get_all_invitations()[iid]
        md = json.loads(data.decode("utf-8"))
        contact = {"petname": invitation_state["petname"],
                   "my_pubkey": invitation_state["my_pubkey"],
                   "their_pubkey": md["my_pubkey"],
                   }
        self._state.add_contact(contact)
        self._wormholes[iid].close()  # now waiting for "closed"

    def wormhole_dispatch_closed(self, _, iid):
        self._wormholes[iid].disownServiceParent()
        del self._wormholes[iid]
        self._state.remove_invitation(iid)

    def handle_app_event(self, args, ack_f):  # sample function
        # Imagine here that the app has received a message (not
        # wormhole-related) from some other server, and needs to act on it.
        # Also imagine that ack_f() is how we tell the sender that they can
        # stop sending the message, or how we ask our poller/subscriber
        # client to send a DELETE message. If the process dies before ack_f()
        # delivers whatever it needs to deliver, then in the next launch,
        # handle_app_event() will be called again.
        stuff = parse(args)  # noqa
        with self._jm.process():
            update_my_state()
            self._jm.queue_outbound(ack_f)


def create(reactor, basedir):
    os.mkdir(basedir)
    s = State.create_empty()
    s.save(os.path.join(basedir, "state.json"))
    return defer.succeed(None)


def run(reactor, basedir):
    a = Agent(basedir, reactor)
    a.startService()
    print("agent listening on http://localhost:8220/")
    d = defer.Deferred()
    return d


if __name__ == "__main__":
    command = sys.argv[1]
    basedir = sys.argv[2]
    if command == "create":
        task.react(create, (basedir,))
    elif command == "run":
        task.react(run, (basedir,))
    else:
        print("Unrecognized subcommand '%s'" % command)
        sys.exit(1)
