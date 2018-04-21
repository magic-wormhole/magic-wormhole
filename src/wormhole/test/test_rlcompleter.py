from __future__ import absolute_import, print_function, unicode_literals

from itertools import count

from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks
from twisted.internet.threads import deferToThread
from twisted.trial import unittest

import mock

from .._rlcompleter import (CodeInputter, _input_code_with_completion,
                            input_with_completion, warn_readline)
from ..errors import AlreadyInputNameplateError, KeyFormatError

APPID = "appid"


class Input(unittest.TestCase):
    @inlineCallbacks
    def test_wrapper(self):
        helper = object()
        trueish = object()
        with mock.patch(
                "wormhole._rlcompleter._input_code_with_completion",
                return_value=trueish) as m:
            used_completion = yield input_with_completion(
                "prompt:", helper, reactor)
        self.assertIs(used_completion, trueish)
        self.assertEqual(m.mock_calls, [mock.call("prompt:", helper, reactor)])
        # note: if this test fails, the warn_readline() message will probably
        # get written to stderr


class Sync(unittest.TestCase):
    # exercise _input_code_with_completion, which uses the blocking builtin
    # "input()" function, hence _input_code_with_completion is usually in a
    # thread with deferToThread

    @mock.patch("wormhole._rlcompleter.CodeInputter")
    @mock.patch("wormhole._rlcompleter.readline", __doc__="I am GNU readline")
    @mock.patch("wormhole._rlcompleter.input", return_value="code")
    def test_readline(self, input, readline, ci):
        c = mock.Mock(name="inhibit parenting")
        c.completer = object()
        trueish = object()
        c.used_completion = trueish
        ci.configure_mock(return_value=c)
        prompt = object()
        input_helper = object()
        reactor = object()
        used = _input_code_with_completion(prompt, input_helper, reactor)
        self.assertIs(used, trueish)
        self.assertEqual(ci.mock_calls, [mock.call(input_helper, reactor)])
        self.assertEqual(c.mock_calls, [mock.call.finish("code")])
        self.assertEqual(input.mock_calls, [mock.call(prompt)])
        self.assertEqual(readline.mock_calls, [
            mock.call.parse_and_bind("tab: complete"),
            mock.call.set_completer(c.completer),
            mock.call.set_completer_delims(""),
        ])

    @mock.patch("wormhole._rlcompleter.CodeInputter")
    @mock.patch("wormhole._rlcompleter.readline")
    @mock.patch("wormhole._rlcompleter.input", return_value="code")
    def test_readline_no_docstring(self, input, readline, ci):
        del readline.__doc__  # when in doubt, it assumes GNU readline
        c = mock.Mock(name="inhibit parenting")
        c.completer = object()
        trueish = object()
        c.used_completion = trueish
        ci.configure_mock(return_value=c)
        prompt = object()
        input_helper = object()
        reactor = object()
        used = _input_code_with_completion(prompt, input_helper, reactor)
        self.assertIs(used, trueish)
        self.assertEqual(ci.mock_calls, [mock.call(input_helper, reactor)])
        self.assertEqual(c.mock_calls, [mock.call.finish("code")])
        self.assertEqual(input.mock_calls, [mock.call(prompt)])
        self.assertEqual(readline.mock_calls, [
            mock.call.parse_and_bind("tab: complete"),
            mock.call.set_completer(c.completer),
            mock.call.set_completer_delims(""),
        ])

    @mock.patch("wormhole._rlcompleter.CodeInputter")
    @mock.patch("wormhole._rlcompleter.readline", __doc__="I am libedit")
    @mock.patch("wormhole._rlcompleter.input", return_value="code")
    def test_libedit(self, input, readline, ci):
        c = mock.Mock(name="inhibit parenting")
        c.completer = object()
        trueish = object()
        c.used_completion = trueish
        ci.configure_mock(return_value=c)
        prompt = object()
        input_helper = object()
        reactor = object()
        used = _input_code_with_completion(prompt, input_helper, reactor)
        self.assertIs(used, trueish)
        self.assertEqual(ci.mock_calls, [mock.call(input_helper, reactor)])
        self.assertEqual(c.mock_calls, [mock.call.finish("code")])
        self.assertEqual(input.mock_calls, [mock.call(prompt)])
        self.assertEqual(readline.mock_calls, [
            mock.call.parse_and_bind("bind ^I rl_complete"),
            mock.call.set_completer(c.completer),
            mock.call.set_completer_delims(""),
        ])

    @mock.patch("wormhole._rlcompleter.CodeInputter")
    @mock.patch("wormhole._rlcompleter.readline", None)
    @mock.patch("wormhole._rlcompleter.input", return_value="code")
    def test_no_readline(self, input, ci):
        c = mock.Mock(name="inhibit parenting")
        c.completer = object()
        trueish = object()
        c.used_completion = trueish
        ci.configure_mock(return_value=c)
        prompt = object()
        input_helper = object()
        reactor = object()
        used = _input_code_with_completion(prompt, input_helper, reactor)
        self.assertIs(used, trueish)
        self.assertEqual(ci.mock_calls, [mock.call(input_helper, reactor)])
        self.assertEqual(c.mock_calls, [mock.call.finish("code")])
        self.assertEqual(input.mock_calls, [mock.call(prompt)])

    @mock.patch("wormhole._rlcompleter.CodeInputter")
    @mock.patch("wormhole._rlcompleter.readline", None)
    @mock.patch("wormhole._rlcompleter.input", return_value=b"code")
    def test_bytes(self, input, ci):
        c = mock.Mock(name="inhibit parenting")
        c.completer = object()
        trueish = object()
        c.used_completion = trueish
        ci.configure_mock(return_value=c)
        prompt = object()
        input_helper = object()
        reactor = object()
        used = _input_code_with_completion(prompt, input_helper, reactor)
        self.assertIs(used, trueish)
        self.assertEqual(ci.mock_calls, [mock.call(input_helper, reactor)])
        self.assertEqual(c.mock_calls, [mock.call.finish(u"code")])
        self.assertEqual(input.mock_calls, [mock.call(prompt)])


def get_completions(c, prefix):
    completions = []
    for state in count(0):
        text = c.completer(prefix, state)
        if text is None:
            return completions
        completions.append(text)


def fake_blockingCallFromThread(f, *a, **kw):
    return f(*a, **kw)


class Completion(unittest.TestCase):
    def test_simple(self):
        # no actual completion
        helper = mock.Mock()
        c = CodeInputter(helper, "reactor")
        c.bcft = fake_blockingCallFromThread
        c.finish("1-code-ghost")
        self.assertFalse(c.used_completion)
        self.assertEqual(helper.mock_calls, [
            mock.call.choose_nameplate("1"),
            mock.call.choose_words("code-ghost")
        ])

    @mock.patch(
        "wormhole._rlcompleter.readline",
        get_completion_type=mock.Mock(return_value=0))
    def test_call(self, readline):
        # check that it calls _commit_and_build_completions correctly
        helper = mock.Mock()
        c = CodeInputter(helper, "reactor")
        c.bcft = fake_blockingCallFromThread

        # pretend nameplates: 1, 12, 34

        # first call will be with "1"
        cabc = mock.Mock(return_value=["1", "12"])
        c._commit_and_build_completions = cabc

        self.assertEqual(get_completions(c, "1"), ["1", "12"])
        self.assertEqual(cabc.mock_calls, [mock.call("1")])

        # then "12"
        cabc.reset_mock()
        cabc.configure_mock(return_value=["12"])
        self.assertEqual(get_completions(c, "12"), ["12"])
        self.assertEqual(cabc.mock_calls, [mock.call("12")])

        # now we have three "a" words: "and", "ark", "aaah!zombies!!"
        cabc.reset_mock()
        cabc.configure_mock(return_value=["aargh", "ark", "aaah!zombies!!"])
        self.assertEqual(
            get_completions(c, "12-a"), ["aargh", "ark", "aaah!zombies!!"])
        self.assertEqual(cabc.mock_calls, [mock.call("12-a")])

        cabc.reset_mock()
        cabc.configure_mock(return_value=["aargh", "aaah!zombies!!"])
        self.assertEqual(
            get_completions(c, "12-aa"), ["aargh", "aaah!zombies!!"])
        self.assertEqual(cabc.mock_calls, [mock.call("12-aa")])

        cabc.reset_mock()
        cabc.configure_mock(return_value=["aaah!zombies!!"])
        self.assertEqual(get_completions(c, "12-aaa"), ["aaah!zombies!!"])
        self.assertEqual(cabc.mock_calls, [mock.call("12-aaa")])

        c.finish("1-code")
        self.assert_(c.used_completion)

    def test_wrap_error(self):
        helper = mock.Mock()
        c = CodeInputter(helper, "reactor")
        c._wrapped_completer = mock.Mock(side_effect=ValueError("oops"))
        with mock.patch("wormhole._rlcompleter.traceback") as traceback:
            with mock.patch("wormhole._rlcompleter.print") as mock_print:
                with self.assertRaises(ValueError) as e:
                    c.completer("text", 0)
        self.assertEqual(traceback.mock_calls, [mock.call.print_exc()])
        self.assertEqual(mock_print.mock_calls,
                         [mock.call("completer exception: oops")])
        self.assertEqual(str(e.exception), "oops")

    @inlineCallbacks
    def test_build_completions(self):
        rn = mock.Mock()
        # InputHelper.get_nameplate_completions returns just the suffixes
        gnc = mock.Mock()  # get_nameplate_completions
        cn = mock.Mock()  # choose_nameplate
        gwc = mock.Mock()  # get_word_completions
        cw = mock.Mock()  # choose_words
        helper = mock.Mock(
            refresh_nameplates=rn,
            get_nameplate_completions=gnc,
            choose_nameplate=cn,
            get_word_completions=gwc,
            choose_words=cw,
        )
        # this needs a real reactor, for blockingCallFromThread
        c = CodeInputter(helper, reactor)
        cabc = c._commit_and_build_completions

        # in this test, we pretend that nameplates 1,12,34 are active.

        # 43 TAB -> nothing (and refresh_nameplates)
        gnc.configure_mock(return_value=[])
        matches = yield deferToThread(cabc, "43")
        self.assertEqual(matches, [])
        self.assertEqual(rn.mock_calls, [mock.call()])
        self.assertEqual(gnc.mock_calls, [mock.call("43")])
        self.assertEqual(cn.mock_calls, [])
        rn.reset_mock()
        gnc.reset_mock()

        # 1 TAB -> 1-, 12- (and refresh_nameplates)
        gnc.configure_mock(return_value=["1-", "12-"])
        matches = yield deferToThread(cabc, "1")
        self.assertEqual(matches, ["1-", "12-"])
        self.assertEqual(rn.mock_calls, [mock.call()])
        self.assertEqual(gnc.mock_calls, [mock.call("1")])
        self.assertEqual(cn.mock_calls, [])
        rn.reset_mock()
        gnc.reset_mock()

        # 12 TAB -> 12- (and refresh_nameplates)
        # I wouldn't mind if it didn't refresh the nameplates here, but meh
        gnc.configure_mock(return_value=["12-"])
        matches = yield deferToThread(cabc, "12")
        self.assertEqual(matches, ["12-"])
        self.assertEqual(rn.mock_calls, [mock.call()])
        self.assertEqual(gnc.mock_calls, [mock.call("12")])
        self.assertEqual(cn.mock_calls, [])
        rn.reset_mock()
        gnc.reset_mock()

        # 12- TAB -> 12- {all words} (claim, no refresh)
        gnc.configure_mock(return_value=["12-"])
        gwc.configure_mock(return_value=["and-", "ark-", "aaah!zombies!!-"])
        matches = yield deferToThread(cabc, "12-")
        self.assertEqual(matches, ["12-aaah!zombies!!-", "12-and-", "12-ark-"])
        self.assertEqual(rn.mock_calls, [])
        self.assertEqual(gnc.mock_calls, [])
        self.assertEqual(cn.mock_calls, [mock.call("12")])
        self.assertEqual(gwc.mock_calls, [mock.call("")])
        cn.reset_mock()
        gwc.reset_mock()

        # TODO: another path with "3 TAB" then "34-an TAB", so the claim
        # happens in the second call (and it waits for the wordlist)

        # 12-a TAB -> 12-and- 12-ark- 12-aaah!zombies!!-
        gnc.configure_mock(side_effect=ValueError)
        gwc.configure_mock(return_value=["and-", "ark-", "aaah!zombies!!-"])
        matches = yield deferToThread(cabc, "12-a")
        # matches are always sorted
        self.assertEqual(matches, ["12-aaah!zombies!!-", "12-and-", "12-ark-"])
        self.assertEqual(rn.mock_calls, [])
        self.assertEqual(gnc.mock_calls, [])
        self.assertEqual(cn.mock_calls, [])
        self.assertEqual(gwc.mock_calls, [mock.call("a")])
        gwc.reset_mock()

        # 12-and-b TAB -> 12-and-bat 12-and-bet 12-and-but
        gnc.configure_mock(side_effect=ValueError)
        # wordlist knows the code length, so doesn't add hyphens to these
        gwc.configure_mock(return_value=["and-bat", "and-bet", "and-but"])
        matches = yield deferToThread(cabc, "12-and-b")
        self.assertEqual(matches, ["12-and-bat", "12-and-bet", "12-and-but"])
        self.assertEqual(rn.mock_calls, [])
        self.assertEqual(gnc.mock_calls, [])
        self.assertEqual(cn.mock_calls, [])
        self.assertEqual(gwc.mock_calls, [mock.call("and-b")])
        gwc.reset_mock()

        yield deferToThread(c.finish, "12-and-bat")
        self.assertEqual(cw.mock_calls, [mock.call("and-bat")])

    def test_incomplete_code(self):
        helper = mock.Mock()
        c = CodeInputter(helper, "reactor")
        c.bcft = fake_blockingCallFromThread
        with self.assertRaises(KeyFormatError) as e:
            c.finish("1")
        self.assertEqual(str(e.exception), "incomplete wormhole code")

    @inlineCallbacks
    def test_rollback_nameplate_during_completion(self):
        helper = mock.Mock()
        gwc = helper.get_word_completions = mock.Mock()
        gwc.configure_mock(return_value=["code", "court"])
        c = CodeInputter(helper, reactor)
        cabc = c._commit_and_build_completions
        matches = yield deferToThread(cabc, "1-co")  # this commits us to 1-
        self.assertEqual(helper.mock_calls, [
            mock.call.choose_nameplate("1"),
            mock.call.when_wordlist_is_available(),
            mock.call.get_word_completions("co")
        ])
        self.assertEqual(matches, ["1-code", "1-court"])
        helper.reset_mock()
        with self.assertRaises(AlreadyInputNameplateError) as e:
            yield deferToThread(cabc, "2-co")
        self.assertEqual(
            str(e.exception), "nameplate (1-) already entered, cannot go back")
        self.assertEqual(helper.mock_calls, [])

    @inlineCallbacks
    def test_rollback_nameplate_during_finish(self):
        helper = mock.Mock()
        gwc = helper.get_word_completions = mock.Mock()
        gwc.configure_mock(return_value=["code", "court"])
        c = CodeInputter(helper, reactor)
        cabc = c._commit_and_build_completions
        matches = yield deferToThread(cabc, "1-co")  # this commits us to 1-
        self.assertEqual(helper.mock_calls, [
            mock.call.choose_nameplate("1"),
            mock.call.when_wordlist_is_available(),
            mock.call.get_word_completions("co")
        ])
        self.assertEqual(matches, ["1-code", "1-court"])
        helper.reset_mock()
        with self.assertRaises(AlreadyInputNameplateError) as e:
            yield deferToThread(c.finish, "2-code")
        self.assertEqual(
            str(e.exception), "nameplate (1-) already entered, cannot go back")
        self.assertEqual(helper.mock_calls, [])

    @mock.patch("wormhole._rlcompleter.stderr")
    def test_warn_readline(self, stderr):
        # there is no good way to test that this function gets used at the
        # right time, since it involves a reactor and a "system event
        # trigger", but let's at least make sure it's invocable
        warn_readline()
        expected = "\nCommand interrupted: please press Return to quit"
        self.assertEqual(stderr.mock_calls,
                         [mock.call.write(expected),
                          mock.call.write("\n")])
