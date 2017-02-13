import os, sys, json
from twisted.internet import task, defer, endpoints
from twisted.application import service, internet
from twisted.web import server, static, resource
from wormhole import journal

class State(object):
    @classmethod
    def create_empty(klass):
        self = klass()
        # to avoid being tripped up by state-mutation side-effect bugs, we
        # hold the serialized state in RAM, and re-deserialize it each time
        # someone asks for a piece of it.
        empty = {"version": 1,
                 "invitations": {}, # iid->invitation_state
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
        tmpfn = fn+".tmp"
        with open(tmpfn, "wb") as f:
            f.write(self._bytes)
        os.rename(tmpfn, fn)

    def _as_data(self):
        return json.loads(bytes.decode("utf-8"))

    @contextlib.contextmanager
    def _mutate(self):
        data = self._as_data()
        yield data # mutable
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
        root.putChild(b"invite", Action(self._invite)) # {petname:}
        root.putChild(b"accept", Action(self._accept)) # {petname:, code:}

        self._state_fn = os.path.join(self._basedir, "state.json")
        self._state = State.from_filename(self._state_fn)

        self._wormholes = {}
        for iid, invitation_state in self._state.get_all_invitations().items():
            def _dispatch(event, *args, **kwargs):
                self._dispatch_wormhole_event(iid, event, *args, **kwargs)
            w = wormhole.journaled_from_data(invitation_state["wormhole"],
                                             reactor=self._reactor,
                                             journal=self._jm,
                                             event_handler=_dispatch)
            self._wormholes[iid] = w
            w.setServiceParent(self)


    def _save_state(self):
        self._state.save_to_filename(self._state_fn)

    def _list_invitations(self):
        inv = self._state.get_all_invitations()
        lines = ["%d: %s" % (iid, inv[iid]) for iid in sorted(inv)]
        return b"\n".join(lines)+b"\n"

    def _invite(self, args):
        print "invite", args
        petname = args["petname"]
        iid = random.randint(1,1000)
        my_pubkey = random.randint(1,1000)
        with self._jm.process():
            def _dispatch(event, *args, **kwargs):
                self._dispatch_wormhole_event(iid, event, *args, **kwargs)
            w = wormhole.journaled(reactor=self._reactor,
                                   journal=self._jm, event_handler=_dispatch)
            self._wormholes[iid] = w
            w.setServiceParent(self)
            w.get_code() # event_handler means code returns via callback
            invitation_state = {"wormhole": w.to_data(),
                                "petname": petname,
                                "my_pubkey": my_pubkey,
                                }
            self._state.add_invitation(iid, invitation_state)
        return b"ok"

    def _accept(self, args):
        print "accept", args
        petname = args["petname"]
        code = args["code"]
        iid = random.randint(1,1000)
        my_pubkey = random.randint(2,2000)
        with self._jm.process():
            def _dispatch(event, *args, **kwargs):
                self._dispatch_wormhole_event(iid, event, *args, **kwargs)
            w = wormhole.wormhole(reactor=self._reactor,
                                  event_dispatcher=_dispatch)
            w.set_code(code)
            md = {"my_pubkey": my_pubkey}
            w.send(json.dumps(md).encode("utf-8"))
            invitation_state = {"wormhole": w.to_data(),
                                "petname": petname,
                                "my_pubkey": my_pubkey,
                                }
            self._state.add_invitation(iid, invitation_state)
        return b"ok"

    def _dispatch_wormhole_event(self, iid, event, *args, **kwargs):
        # we're already in a jm.process() context
        invitation_state = self._state.get_all_invitations()[iid]
        if event == "got-code":
            (code,) = args
            invitation_state["code"] = code
            self._state.update_invitation(iid, invitation_state)
            self._wormholes[iid].set_code(code)
            # notify UI subscribers to update the display
        elif event == "got-data":
            (data,) = args
            md = json.loads(data.decode("utf-8"))
            contact = {"petname": invitation_state["petname"],
                       "my_pubkey": invitation_state["my_pubkey"],
                       "their_pubkey": md["my_pubkey"],
                       }
            self._state.add_contact(contact)
            self._wormholes[iid].close()
        elif event == "closed":
            self._wormholes[iid].disownServiceParent()
            del self._wormholes[iid]
            self._state.remove_invitation(iid)
            

def create(reactor, basedir):
    os.mkdir(basedir)
    s = State.create_empty()
    s.save(os.path.join(basedir, "state.json"))
    return defer.succeed(None)

def run(reactor, basedir):
    a = Agent(basedir, reactor)
    a.startService()
    print "agent listening on http://localhost:8220/"
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
        print "Unrecognized subcommand '%s'" % command
        sys.exit(1)


