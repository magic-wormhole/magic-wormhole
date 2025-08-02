from itertools import count

from twisted.internet import reactor
from twisted.internet.threads import deferToThread

from unittest import mock

from pytest_twisted import ensureDeferred

from .._rlcompleter import (CodeInputter, _input_code_with_completion,
                            input_with_completion, warn_readline)
from ..errors import AlreadyInputNameplateError, KeyFormatError
import pytest

APPID = "appid"


@ensureDeferred
async def test_wrapper():
    helper = object()
    trueish = object()
    with mock.patch(
            "wormhole._rlcompleter._input_code_with_completion",
            return_value=trueish) as m:
        used_completion = await input_with_completion(
            "prompt:", helper, reactor)
    assert used_completion is trueish
    assert m.mock_calls == [mock.call("prompt:", helper, reactor)]
    # note: if this test fails, the warn_readline() message will probably
    # get written to stderr


# exercise _input_code_with_completion, which uses the blocking builtin
# "input()" function, hence _input_code_with_completion is usually in a
# thread with deferToThread

@mock.patch("wormhole._rlcompleter.CodeInputter")
@mock.patch("wormhole._rlcompleter.readline", __doc__="I am GNU readline")
@mock.patch("wormhole._rlcompleter.input", return_value="code")
def test_readline(input, readline, ci):
    c = mock.Mock(name="inhibit parenting")
    c.completer = object()
    trueish = object()
    c.used_completion = trueish
    ci.configure_mock(return_value=c)
    prompt = object()
    input_helper = object()
    reactor = object()
    used = _input_code_with_completion(prompt, input_helper, reactor)
    assert used is trueish
    assert ci.mock_calls == [mock.call(input_helper, reactor)]
    assert c.mock_calls == [mock.call.finish("code")]
    assert input.mock_calls == [mock.call(prompt)]
    assert readline.mock_calls == [
        mock.call.parse_and_bind("tab: complete"),
        mock.call.set_completer(c.completer),
        mock.call.set_completer_delims(""),
    ]

@mock.patch("wormhole._rlcompleter.CodeInputter")
@mock.patch("wormhole._rlcompleter.readline")
@mock.patch("wormhole._rlcompleter.input", return_value="code")
def test_readline_no_docstring(input, readline, ci):
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
    assert used is trueish
    assert ci.mock_calls == [mock.call(input_helper, reactor)]
    assert c.mock_calls == [mock.call.finish("code")]
    assert input.mock_calls == [mock.call(prompt)]
    assert readline.mock_calls == [
        mock.call.parse_and_bind("tab: complete"),
        mock.call.set_completer(c.completer),
        mock.call.set_completer_delims(""),
    ]

@mock.patch("wormhole._rlcompleter.CodeInputter")
@mock.patch("wormhole._rlcompleter.readline", __doc__="I am libedit")
@mock.patch("wormhole._rlcompleter.input", return_value="code")
def test_libedit(input, readline, ci):
    c = mock.Mock(name="inhibit parenting")
    c.completer = object()
    trueish = object()
    c.used_completion = trueish
    ci.configure_mock(return_value=c)
    prompt = object()
    input_helper = object()
    reactor = object()
    used = _input_code_with_completion(prompt, input_helper, reactor)
    assert used is trueish
    assert ci.mock_calls == [mock.call(input_helper, reactor)]
    assert c.mock_calls == [mock.call.finish("code")]
    assert input.mock_calls == [mock.call(prompt)]
    assert readline.mock_calls == [
        mock.call.parse_and_bind("bind ^I rl_complete"),
        mock.call.set_completer(c.completer),
        mock.call.set_completer_delims(""),
    ]

@mock.patch("wormhole._rlcompleter.CodeInputter")
@mock.patch("wormhole._rlcompleter.readline", None)
@mock.patch("wormhole._rlcompleter.input", return_value="code")
def test_no_readline(input, ci):
    c = mock.Mock(name="inhibit parenting")
    c.completer = object()
    trueish = object()
    c.used_completion = trueish
    ci.configure_mock(return_value=c)
    prompt = object()
    input_helper = object()
    reactor = object()
    used = _input_code_with_completion(prompt, input_helper, reactor)
    assert used is trueish
    assert ci.mock_calls == [mock.call(input_helper, reactor)]
    assert c.mock_calls == [mock.call.finish("code")]
    assert input.mock_calls == [mock.call(prompt)]

@mock.patch("wormhole._rlcompleter.CodeInputter")
@mock.patch("wormhole._rlcompleter.readline", None)
@mock.patch("wormhole._rlcompleter.input", return_value=b"code")
def test_bytes(input, ci):
    c = mock.Mock(name="inhibit parenting")
    c.completer = object()
    trueish = object()
    c.used_completion = trueish
    ci.configure_mock(return_value=c)
    prompt = object()
    input_helper = object()
    reactor = object()
    used = _input_code_with_completion(prompt, input_helper, reactor)
    assert used is trueish
    assert ci.mock_calls == [mock.call(input_helper, reactor)]
    assert c.mock_calls == [mock.call.finish("code")]
    assert input.mock_calls == [mock.call(prompt)]


def get_completions(c, prefix):
    completions = []
    for state in count(0):
        text = c.completer(prefix, state)
        if text is None:
            return completions
        completions.append(text)


def fake_blockingCallFromThread(f, *a, **kw):
    return f(*a, **kw)


def test_simple():
    # no actual completion
    helper = mock.Mock()
    c = CodeInputter(helper, "reactor")
    c.bcft = fake_blockingCallFromThread
    c.finish("1-code-ghost")
    assert not c.used_completion
    assert helper.mock_calls == [
        mock.call.choose_nameplate("1"),
        mock.call.choose_words("code-ghost")
    ]

@mock.patch(
    "wormhole._rlcompleter.readline",
    get_completion_type=mock.Mock(return_value=0))
def test_call(readline):
    # check that it calls _commit_and_build_completions correctly
    helper = mock.Mock()
    c = CodeInputter(helper, "reactor")
    c.bcft = fake_blockingCallFromThread

    # pretend nameplates: 1, 12, 34

    # first call will be with "1"
    cabc = mock.Mock(return_value=["1", "12"])
    c._commit_and_build_completions = cabc

    assert get_completions(c, "1") == ["1", "12"]
    assert cabc.mock_calls == [mock.call("1")]

    # then "12"
    cabc.reset_mock()
    cabc.configure_mock(return_value=["12"])
    assert get_completions(c, "12") == ["12"]
    assert cabc.mock_calls == [mock.call("12")]

    # now we have three "a" words: "and", "ark", "aaah!zombies!!"
    cabc.reset_mock()
    cabc.configure_mock(return_value=["aargh", "ark", "aaah!zombies!!"])
    assert get_completions(c, "12-a") == ["aargh", "ark", "aaah!zombies!!"]
    assert cabc.mock_calls == [mock.call("12-a")]

    cabc.reset_mock()
    cabc.configure_mock(return_value=["aargh", "aaah!zombies!!"])
    assert get_completions(c, "12-aa") == ["aargh", "aaah!zombies!!"]
    assert cabc.mock_calls == [mock.call("12-aa")]

    cabc.reset_mock()
    cabc.configure_mock(return_value=["aaah!zombies!!"])
    assert get_completions(c, "12-aaa") == ["aaah!zombies!!"]
    assert cabc.mock_calls == [mock.call("12-aaa")]

    c.finish("1-code")
    assert c.used_completion

def test_wrap_error():
    helper = mock.Mock()
    c = CodeInputter(helper, "reactor")
    c._wrapped_completer = mock.Mock(side_effect=ValueError("oops"))
    with mock.patch("wormhole._rlcompleter.traceback") as traceback:
        with mock.patch("wormhole._rlcompleter.print") as mock_print:
            with pytest.raises(ValueError) as e:
                c.completer("text", 0)
    assert traceback.mock_calls == [mock.call.print_exc()]
    assert mock_print.mock_calls == \
                     [mock.call("completer exception: oops")]
    assert str(e.value) == "oops"

@ensureDeferred
async def test_build_completions():
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
    matches = await deferToThread(cabc, "43")
    assert matches == []
    assert rn.mock_calls == [mock.call()]
    assert gnc.mock_calls == [mock.call("43")]
    assert cn.mock_calls == []
    rn.reset_mock()
    gnc.reset_mock()

    # 1 TAB -> 1-, 12- (and refresh_nameplates)
    gnc.configure_mock(return_value=["1-", "12-"])
    matches = await deferToThread(cabc, "1")
    assert matches == ["1-", "12-"]
    assert rn.mock_calls == [mock.call()]
    assert gnc.mock_calls == [mock.call("1")]
    assert cn.mock_calls == []
    rn.reset_mock()
    gnc.reset_mock()

    # 12 TAB -> 12- (and refresh_nameplates)
    # I wouldn't mind if it didn't refresh the nameplates here, but meh
    gnc.configure_mock(return_value=["12-"])
    matches = await deferToThread(cabc, "12")
    assert matches == ["12-"]
    assert rn.mock_calls == [mock.call()]
    assert gnc.mock_calls == [mock.call("12")]
    assert cn.mock_calls == []
    rn.reset_mock()
    gnc.reset_mock()

    # 12- TAB -> 12- {all words} (claim, no refresh)
    gnc.configure_mock(return_value=["12-"])
    gwc.configure_mock(return_value=["and-", "ark-", "aaah!zombies!!-"])
    matches = await deferToThread(cabc, "12-")
    assert matches == ["12-aaah!zombies!!-", "12-and-", "12-ark-"]
    assert rn.mock_calls == []
    assert gnc.mock_calls == []
    assert cn.mock_calls == [mock.call("12")]
    assert gwc.mock_calls == [mock.call("")]
    cn.reset_mock()
    gwc.reset_mock()

    # TODO: another path with "3 TAB" then "34-an TAB", so the claim
    # happens in the second call (and it waits for the wordlist)

    # 12-a TAB -> 12-and- 12-ark- 12-aaah!zombies!!-
    gnc.configure_mock(side_effect=ValueError)
    gwc.configure_mock(return_value=["and-", "ark-", "aaah!zombies!!-"])
    matches = await deferToThread(cabc, "12-a")
    # matches are always sorted
    assert matches == ["12-aaah!zombies!!-", "12-and-", "12-ark-"]
    assert rn.mock_calls == []
    assert gnc.mock_calls == []
    assert cn.mock_calls == []
    assert gwc.mock_calls == [mock.call("a")]
    gwc.reset_mock()

    # 12-and-b TAB -> 12-and-bat 12-and-bet 12-and-but
    gnc.configure_mock(side_effect=ValueError)
    # wordlist knows the code length, so doesn't add hyphens to these
    gwc.configure_mock(return_value=["and-bat", "and-bet", "and-but"])
    matches = await deferToThread(cabc, "12-and-b")
    assert matches == ["12-and-bat", "12-and-bet", "12-and-but"]
    assert rn.mock_calls == []
    assert gnc.mock_calls == []
    assert cn.mock_calls == []
    assert gwc.mock_calls == [mock.call("and-b")]
    gwc.reset_mock()

    await deferToThread(c.finish, "12-and-bat")
    assert cw.mock_calls == [mock.call("and-bat")]

def test_incomplete_code():
    helper = mock.Mock()
    c = CodeInputter(helper, "reactor")
    c.bcft = fake_blockingCallFromThread
    with pytest.raises(KeyFormatError) as e:
        c.finish("1")
    assert str(e.value) == "incomplete wormhole code"

@ensureDeferred
async def test_rollback_nameplate_during_completion():
    helper = mock.Mock()
    gwc = helper.get_word_completions = mock.Mock()
    gwc.configure_mock(return_value=["code", "court"])
    c = CodeInputter(helper, reactor)
    cabc = c._commit_and_build_completions
    matches = await deferToThread(cabc, "1-co")  # this commits us to 1-
    assert helper.mock_calls == [
        mock.call.choose_nameplate("1"),
        mock.call.when_wordlist_is_available(),
        mock.call.get_word_completions("co")
    ]
    assert matches == ["1-code", "1-court"]
    helper.reset_mock()
    with pytest.raises(AlreadyInputNameplateError) as e:
        await deferToThread(cabc, "2-co")
    assert str(e.value) == "nameplate (1-) already entered, cannot go back"
    assert helper.mock_calls == []

@ensureDeferred
async def test_rollback_nameplate_during_finish():
    helper = mock.Mock()
    gwc = helper.get_word_completions = mock.Mock()
    gwc.configure_mock(return_value=["code", "court"])
    c = CodeInputter(helper, reactor)
    cabc = c._commit_and_build_completions
    matches = await deferToThread(cabc, "1-co")  # this commits us to 1-
    assert helper.mock_calls == [
        mock.call.choose_nameplate("1"),
        mock.call.when_wordlist_is_available(),
        mock.call.get_word_completions("co")
    ]
    assert matches == ["1-code", "1-court"]
    helper.reset_mock()
    with pytest.raises(AlreadyInputNameplateError) as e:
        await deferToThread(c.finish, "2-code")
    assert str(e.value) == "nameplate (1-) already entered, cannot go back"
    assert helper.mock_calls == []

@mock.patch("wormhole._rlcompleter.stderr")
def test_warn_readline(stderr):
    # there is no good way to test that this function gets used at the
    # right time, since it involves a reactor and a "system event
    # trigger", but let's at least make sure it's invocable
    warn_readline()
    expected = "\nCommand interrupted: please press Return to quit"
    assert stderr.mock_calls == \
                     [mock.call.write(expected),
                      mock.call.write("\n")]
