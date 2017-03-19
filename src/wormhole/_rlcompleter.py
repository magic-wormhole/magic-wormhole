from __future__ import print_function, unicode_literals
import six
from attr import attrs, attrib
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.threads import deferToThread, blockingCallFromThread

@attrs
class CodeInputter:
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
            raise e

    def completer(self, text, state):
        self.used_completion = True
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

def input_code_with_completion(prompt, input_helper, code_length):
    try:
        import readline
        c = CodeInputter(input_helper)
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

@inlineCallbacks
def rlcompleter_helper(prompt, input_helper, reactor):
    def warn_readline():
        pass
    t = reactor.addSystemEventTrigger("before", "shutdown", warn_readline)
    res = yield deferToThread(input_code_with_completion, prompt, input_helper)
    (code, used_completion) = res
    reactor.removeSystemEventTrigger(t)
    returnValue(used_completion)
