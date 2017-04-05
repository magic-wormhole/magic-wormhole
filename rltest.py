from __future__ import print_function
import traceback
from twisted.internet import reactor, protocol, endpoints, defer
from twisted.protocols import basic
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.threads import deferToThread, blockingCallFromThread
from six.moves import input
import readline

class Command(basic.LineOnlyReceiver):
    #delimiter = b'\n'
    def connectionMade(self):
        self.factory.connected(self)
    def connectionLost(self, reason):
        self.factory.disconnected()
    def lineReceived(self, line):
        self.factory.got_line(line)

class FactoryBoss(protocol.ServerFactory, object):
    protocol = Command
    def __init__(self):
        super(FactoryBoss, self).__init__()
        self.command_d = None
    def connected(self, p):
        self.p = p
    def disconnected(self):
        self.p = None
    def debug(self, msg):
        if self.p:
            self.p.transport.write(msg+"\r\n")
    def got_line(self, line):
        if self.command_d:
            d, self.command_d = self.command_d, None
            d.callback(line.split())
            self.debug(" match delivered")
        else:
            self.debug(" command ignored: nothing waiting")

    def completer(self, text, state):
        try:
            return self._wrapped_completer(text, state)
        except Exception as e:
            # completer exceptions are normally silently discarded, which
            # makes debugging challenging
            print()
            print("completer exception: %s" % e)
            traceback.print_exc()
            raise e

    def get_matches(self, text, ct):
        return blockingCallFromThread(reactor, self._get_matches, text, ct)

    @inlineCallbacks
    def _get_matches(self, text, ct):
        self.debug("get_matches(%s) [%s]" % (ct, text))
        self.command_d = defer.Deferred()
        matches = yield self.command_d
        self.debug(" thanks")
        returnValue(matches)

    def _wrapped_completer(self, text, state):
        ct = readline.get_completion_type()
        if state == 0:
            self.debug("completer starting (%s) (state=0) (ct=%d)" % (text, ct))
            self._matches = self.get_matches(text, ct)
            self.debug(" matches: %s" % " ".join(["'%s'" % m for m in self._matches]))
        else:
            self.debug(" s%d t'%s' ct=%d" % (state, text, ct))

        if state >= len(self._matches):
            self.debug("  returning None")
            return None
        self.debug("  returning '%s'" % self._matches[state])
        return self._matches[state]

    @inlineCallbacks
    def do_readline(self):
        while True:
            line = yield deferToThread(self._do_readline)
            print("RL:", line)

    def _do_readline(self):
        return input("prompt: ")


f = FactoryBoss()
ep = endpoints.serverFromString(reactor, "tcp:9081")
ep.listen(f)

if readline.__doc__ and "libedit" in readline.__doc__:
    readline.parse_and_bind("bind ^I rl_complete")
else:
    readline.parse_and_bind("tab: complete")
    readline.parse_and_bind("set show-all-if-ambiguous on")
    readline.parse_and_bind("set show-all-if-unmodified on")
readline.set_completer(f.completer)
readline.set_completer_delims("")

f.do_readline()

reactor.run()

# first TAB (one match): ct=9, append everything, do not beep
# first TAB (some letters in common): ct=9, append common letters, beep
# first TAB (nothing in common): ct=9, beep
# a second TAB after nothing has changed: ct=63, display matches, beep
# counter is reset by typing anything, or when readline appends common
#  letters
