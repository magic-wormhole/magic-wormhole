#import sys
from twisted.python import log, failure
from twisted.internet import reactor, defer, protocol
from twisted.application import service
from twisted.protocols import basic
from twisted.web.client import Agent, ResponseDone
from twisted.web.http_headers import Headers
from cgi import parse_header
from .eventual import eventually

#if sys.version_info[0] == 2:
#    to_unicode = unicode
#else:
#    to_unicode = str

class EventSourceParser(basic.LineOnlyReceiver):
    # http://www.w3.org/TR/eventsource/
    delimiter = b"\n"

    def __init__(self, handler):
        self.current_field = None
        self.current_lines = []
        self.handler = handler
        self.done_deferred = defer.Deferred()
        self.eventtype = u"message"
        self.encoding = "utf-8"

    def set_encoding(self, encoding):
        self.encoding = encoding

    def connectionLost(self, why):
        if why.check(ResponseDone):
            why = None
        self.done_deferred.callback(why)

    def dataReceived(self, data):
        # exceptions here aren't being logged properly, and tests will hang
        # rather than halt. I suspect twisted.web._newclient's
        # HTTP11ClientProtocol.dataReceived(), which catches everything and
        # responds with self._giveUp() but doesn't log.err.
        try:
            basic.LineOnlyReceiver.dataReceived(self, data)
        except:
            log.err()
            raise

    def lineReceived(self, line):
        #line = to_unicode(line, self.encoding)
        line = line.decode(self.encoding)
        if not line:
            # blank line ends the field: deliver event, reset for next
            self.eventReceived(self.eventtype, "\n".join(self.current_lines))
            self.eventtype = u"message"
            self.current_lines[:] = []
            return
        if u":" in line:
            fieldname, data = line.split(u":", 1)
            if data.startswith(u" "):
                data = data[1:]
        else:
            fieldname = line
            data = u""
        if fieldname == u"event":
            self.eventtype = data
        elif fieldname == u"data":
            self.current_lines.append(data)
        elif fieldname in (u"id", u"retry"):
            # documented but unhandled
            pass
        else:
            log.msg("weird fieldname", fieldname, data)

    def eventReceived(self, eventtype, data):
        self.handler(eventtype, data)

class EventSourceError(Exception):
    pass

# es = EventSource(url, handler)
# d = es.start()
# es.cancel()

class EventSource: # TODO: service.Service
    def __init__(self, url, handler, when_connected=None, agent=None):
        assert isinstance(url, type(u""))
        self.url = url
        self.handler = handler
        self.when_connected = when_connected
        self.started = False
        self.cancelled = False
        self.proto = EventSourceParser(self.handler)
        if not agent:
            agent = Agent(reactor)
        self.agent = agent

    def start(self):
        assert not self.started, "single-use"
        self.started = True
        assert self.url
        d = self.agent.request(b"GET", self.url.encode("utf-8"),
                               Headers({b"accept": [b"text/event-stream"]}))
        d.addCallback(self._connected)
        return d

    def _connected(self, resp):
        if resp.code != 200:
            raise EventSourceError("%d: %s" % (resp.code, resp.phrase))
        if self.when_connected:
            self.when_connected()
        default_ct = "text/event-stream; charset=utf-8"
        ct_headers = resp.headers.getRawHeaders("content-type", [default_ct])
        ct, ct_params = parse_header(ct_headers[0])
        assert ct == "text/event-stream", ct
        self.proto.set_encoding(ct_params.get("charset", "utf-8"))
        resp.deliverBody(self.proto)
        if self.cancelled:
            self.kill_connection()
        return self.proto.done_deferred

    def cancel(self):
        self.cancelled = True
        if not self.proto.transport:
            # _connected hasn't been called yet, but that self.cancelled
            # should take care of it when the connection is established
            def kill(data):
                # this should kill it as soon as any data is delivered
                raise ValueError("dead")
            self.proto.dataReceived = kill # just in case
            return
        self.kill_connection()

    def kill_connection(self):
        if (hasattr(self.proto.transport, "_producer")
            and self.proto.transport._producer):
            # This is gross and fragile. We need a clean way to stop the
            # client connection. p.transport is a
            # twisted.web._newclient.TransportProxyProducer , and its
            # ._producer is the tcp.Port.
            self.proto.transport._producer.loseConnection()
        else:
            log.err("get_events: unable to stop connection")
            # oh well
            #err = EventSourceError("unable to cancel")
            try:
                self.proto.done_deferred.callback(None)
            except defer.AlreadyCalledError:
                pass


class Connector:
    # behave enough like an IConnector to appease ReconnectingClientFactory
    def __init__(self, res):
        self.res = res
    def connect(self):
        self.res._maybeStart()
    def stopConnecting(self):
        self.res._stop_eventsource()

class ReconnectingEventSource(service.MultiService,
                              protocol.ReconnectingClientFactory):
    def __init__(self, url, handler, agent=None):
        service.MultiService.__init__(self)
        # we don't use any of the basic Factory/ClientFactory methods of
        # this, just the ReconnectingClientFactory.retry, stopTrying, and
        # resetDelay methods.

        self.url = url
        self.handler = handler
        self.agent = agent
        # IService provides self.running, toggled by {start,stop}Service.
        # self.active is toggled by {,de}activate. If both .running and
        # .active are True, then we want to have an outstanding EventSource
        # and will start one if necessary. If either is False, then we don't
        # want one to be outstanding, and will initiate shutdown.
        self.active = False
        self.connector = Connector(self)
        self.es = None # set we have an outstanding EventSource
        self.when_stopped = [] # list of Deferreds

    def isStopped(self):
        return not self.es

    def startService(self):
        service.MultiService.startService(self) # sets self.running
        self._maybeStart()

    def stopService(self):
        # clears self.running
        d = defer.maybeDeferred(service.MultiService.stopService, self)
        d.addCallback(self._maybeStop)
        return d

    def activate(self):
        assert not self.active
        self.active = True
        self._maybeStart()

    def deactivate(self):
        assert self.active # XXX
        self.active = False
        return self._maybeStop()

    def _maybeStart(self):
        if not (self.active and self.running):
            return
        self.continueTrying = True
        self.es = EventSource(self.url, self.handler, self.resetDelay,
                              agent=self.agent)
        d = self.es.start()
        d.addBoth(self._stopped)

    def _stopped(self, res):
        self.es = None
        # we might have stopped because of a connection error, or because of
        # an intentional shutdown.
        if self.active and self.running:
            # we still want to be connected, so schedule a reconnection
            if isinstance(res, failure.Failure):
                log.err(res)
            self.retry() # will eventually call _maybeStart
            return
        # intentional shutdown
        self.stopTrying()
        for d in self.when_stopped:
            eventually(d.callback, None)
        self.when_stopped = []

    def _stop_eventsource(self):
        if self.es:
            eventually(self.es.cancel)

    def _maybeStop(self, _=None):
        self.stopTrying() # cancels timer, calls _stop_eventsource()
        if not self.es:
            return defer.succeed(None)
        d = defer.Deferred()
        self.when_stopped.append(d)
        return d
