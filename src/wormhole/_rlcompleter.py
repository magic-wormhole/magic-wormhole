from __future__ import print_function, unicode_literals
import sys
import six
from attr import attrs, attrib
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.threads import deferToThread, blockingCallFromThread

@attrs
class CodeInputter(object):
    _input_helper = attrib()
    _reactor = attrib()
    def __attrs_post_init__(self):
        self.used_completion = False
        self._matches = None
        self._committed_nameplate = None

    def bcft(self, f, *a, **kw):
        return blockingCallFromThread(self._reactor, f, *a, **kw)

    def wrap_completer(self, text, state):
        try:
            return self.completer(text, state)
        except Exception as e:
            # completer exceptions are normally silently discarded, which
            # makes debugging challenging
            print("completer exception: %s" % e)
            import traceback
            traceback.print_exc()
            raise e

    def completer(self, text, state):
        self.used_completion = True
        # debug
        #import readline
        #if state == 0:
        #    print("", file=sys.stderr)
        #print("completer: '%s' %d '%d'" % (text, state,
        #                                   readline.get_completion_type()),
        #      file=sys.stderr)
        #sys.stderr.flush()

        if state > 0:
            # just use the values we decided last time
            if state >= len(self._matches):
                return None
            return self._matches[state]

        if not self._committed_nameplate:
            self.bcft(self._input_helper.refresh_nameplates)

        # now figure out new matches
        if not "-" in text:
            completions = self.bcft(self._input_helper.get_nameplate_completions,
                                    text)
            # TODO: does rlcompleter want full strings, or the next suffix?
            self._matches = sorted(completions)
        else:
            nameplate, words = text.split("-", 1)
            if self._committed_nameplate:
                if nameplate != self._committed_nameplate:
                    # they deleted past the committment point: we can't use
                    # this. For now, bail, but in the future let's find a
                    # gentler way to encourage them to not do that.
                    raise ValueError("nameplate (NN-) already entered, cannot go back")
                # they've just committed to this nameplate
                self.bcft(self._input_helper.choose_nameplate, nameplate)
                self._committed_nameplate = nameplate
            completions = self.bcft(self._input_helper.get_word_completions,
                                    words)
            self._matches = sorted(completions)

        #print(" match: '%s'" % self._matches[state], file=sys.stderr)
        #sys.stderr.flush()
        return self._matches[state]

def input_code_with_completion(prompt, input_helper, reactor):
    try:
        import readline
        c = CodeInputter(input_helper, reactor)
        if readline.__doc__ and "libedit" in readline.__doc__:
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")
        readline.set_completer(c.wrap_completer)
        readline.set_completer_delims("")
    except ImportError:
        c = None
    code = six.moves.input(prompt)
    # Code is str(bytes) on py2, and str(unicode) on py3. We want unicode.
    if isinstance(code, bytes):
        code = code.decode("utf-8")
    nameplate, words = code.split("-", 1)
    input_helper.choose_words(words)
    used_completion = c.used_completion if c else False
    return (code, used_completion)

def warn_readline():
    # When our process receives a SIGINT, Twisted's SIGINT handler will
    # stop the reactor and wait for all threads to terminate before the
    # process exits. However, if we were waiting for
    # input_code_with_completion() when SIGINT happened, the readline
    # thread will be blocked waiting for something on stdin. Trick the
    # user into satisfying the blocking read so we can exit.
    print("\nCommand interrupted: please press Return to quit",
          file=sys.stderr)

    # Other potential approaches to this problem:
    # * hard-terminate our process with os._exit(1), but make sure the
    #   tty gets reset to a normal mode ("cooked"?) first, so that the
    #   next shell command the user types is echoed correctly
    # * track down the thread (t.p.threadable.getThreadID from inside the
    #   thread), get a cffi binding to pthread_kill, deliver SIGINT to it
    # * allocate a pty pair (pty.openpty), replace sys.stdin with the
    #   slave, build a pty bridge that copies bytes (and other PTY
    #   things) from the real stdin to the master, then close the slave
    #   at shutdown, so readline sees EOF
    # * write tab-completion and basic editing (TTY raw mode,
    #   backspace-is-erase) without readline, probably with curses or
    #   twisted.conch.insults
    # * write a separate program to get codes (maybe just "wormhole
    #   --internal-get-code"), run it as a subprocess, let it inherit
    #   stdin/stdout, send it SIGINT when we receive SIGINT ourselves. It
    #   needs an RPC mechanism (over some extra file descriptors) to ask
    #   us to fetch the current nameplate_id list.
    #
    # Note that hard-terminating our process with os.kill(os.getpid(),
    # signal.SIGKILL), or SIGTERM, doesn't seem to work: the thread
    # doesn't see the signal, and we must still wait for stdin to make
    # readline finish.

@inlineCallbacks
def rlcompleter_helper(prompt, input_helper, reactor):
    t = reactor.addSystemEventTrigger("before", "shutdown", warn_readline)
    res = yield deferToThread(input_code_with_completion, prompt, input_helper,
                              reactor)
    (code, used_completion) = res
    reactor.removeSystemEventTrigger(t)
    returnValue(used_completion)
