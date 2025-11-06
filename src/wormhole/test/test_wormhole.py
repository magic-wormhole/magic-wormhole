import io
import re

from twisted.internet.defer import gatherResults
from twisted.internet.error import ConnectionRefusedError

from unittest import mock
from pytest_twisted import ensureDeferred

from .. import _rendezvous, wormhole
from ..errors import (KeyFormatError, LonelyError, NoKeyError,
                      OnlyOneCodeError, ServerConnectionError, WormholeClosed,
                      WrongPasswordError)
from ..eventual import EventualQueue
from ..transit import allocate_tcp_port
from .common import poll_until
import pytest

APPID = "appid"

# event orderings to exercise:
#
# * normal sender: set_code, send_phase1, connected, claimed, learn_msg2,
#   learn_phase1
# * normal receiver (argv[2]=code): set_code, connected, learn_msg1,
#   learn_phase1, send_phase1,
# * normal receiver (readline): connected, input_code
# *
# * set_code, then connected
# * connected, receive_pake, send_phase, set_code


class Delegate:
    def __init__(self):
        self.welcome = None
        self.code = None
        self.key = None
        self.verifier = None
        self.versions = None
        self.messages = []
        self.closed = None

    def wormhole_got_welcome(self, welcome):
        self.welcome = welcome

    def wormhole_got_code(self, code):
        self.code = code

    def wormhole_got_unverified_key(self, key):
        self.key = key

    def wormhole_got_verifier(self, verifier):
        self.verifier = verifier

    def wormhole_got_versions(self, versions):
        self.versions = versions

    def wormhole_got_message(self, data):
        self.messages.append(data)

    def wormhole_closed(self, result):
        self.closed = result


@ensureDeferred
async def test_delegated(reactor, mailbox):
    dg = Delegate()
    w1 = wormhole.create(APPID, mailbox.url, reactor, delegate=dg)
    # w1.debug_set_trace("W1")
    with pytest.raises(NoKeyError):
        w1.derive_key("purpose", 12)
    w1.set_code("1-abc")
    assert dg.code == "1-abc"
    w2 = wormhole.create(APPID, mailbox.url, reactor)
    w2.set_code(dg.code)
    await poll_until(lambda: dg.key is not None)
    await poll_until(lambda: dg.verifier is not None)
    await poll_until(lambda: dg.versions is not None)

    w1.send_message(b"ping")
    got = await w2.get_message()
    assert got == b"ping"
    w2.send_message(b"pong")
    await poll_until(lambda: dg.messages)
    assert dg.messages[0] == b"pong"

    key1 = w1.derive_key("purpose", 16)
    assert len(key1) == 16
    assert type(key1) is bytes
    with pytest.raises(TypeError):
        w1.derive_key(b"not unicode", 16)
    with pytest.raises(TypeError):
        w1.derive_key(12345, 16)

    w1.close()
    await w2.close()


@ensureDeferred
async def test_delegate_allocate_code(reactor, mailbox):
    dg = Delegate()
    w1 = wormhole.create(APPID, mailbox.url, reactor, delegate=dg)
    w1.allocate_code()
    await poll_until(lambda: dg.code is not None)
    w1.close()


@ensureDeferred
async def test_delegate_input_code(reactor, mailbox):
    dg = Delegate()
    w1 = wormhole.create(APPID, mailbox.url, reactor, delegate=dg)
    h = w1.input_code()
    h.choose_nameplate("123")
    h.choose_words("purple-elephant")
    await poll_until(lambda: dg.code is not None)
    w1.close()


# integration test, with a real server

async def doBoth(d1, d2):
    return await gatherResults([d1, d2], True)


@ensureDeferred
async def test_allocate_default(reactor, mailbox):
    w1 = wormhole.create(APPID, mailbox.url, reactor)
    w1.allocate_code()
    code = await w1.get_code()
    mo = re.search(r"^\d+-\w+-\w+$", code)
    assert mo, code
    # w.close() fails because we closed before connecting
    with pytest.raises(LonelyError):
        await w1.close()


@ensureDeferred
async def test_allocate_more_words(reactor, mailbox):
    w1 = wormhole.create(APPID, mailbox.url, reactor)
    w1.allocate_code(3)
    code = await w1.get_code()
    mo = re.search(r"^\d+-\w+-\w+-\w+$", code)
    assert mo, code
    with pytest.raises(LonelyError):
        await w1.close()


@ensureDeferred
async def test_basic(reactor, mailbox):
    w1 = wormhole.create(APPID, mailbox.url, reactor)
    # w1.debug_set_trace("W1")
    with pytest.raises(NoKeyError):
        w1.derive_key("purpose", 12)

    w2 = wormhole.create(APPID, mailbox.url, reactor)
    # w2.debug_set_trace("  W2")
    w1.allocate_code()
    code = await w1.get_code()
    w2.set_code(code)

    await w1.get_unverified_key()
    await w2.get_unverified_key()

    key1 = w1.derive_key("purpose", 16)
    assert len(key1) == 16
    assert type(key1) is bytes
    with pytest.raises(TypeError):
        w1.derive_key(b"not unicode", 16)
    with pytest.raises(TypeError):
        w1.derive_key(12345, 16)

    verifier1 = await w1.get_verifier()
    verifier2 = await w2.get_verifier()
    assert verifier1 == verifier2

    versions1 = await w1.get_versions()
    versions2 = await w2.get_versions()
    # app-versions are exercised properly in test_versions, this just
    # tests the defaults
    assert versions1 == {}
    assert versions2 == {}

    w1.send_message(b"data1")
    w2.send_message(b"data2")
    dataX = await w1.get_message()
    dataY = await w2.get_message()
    assert dataX == b"data2"
    assert dataY == b"data1"

    versions1_again = await w1.get_versions()
    assert versions1 == versions1_again

    c1 = await w1.close()
    assert c1 == "happy"
    c2 = await w2.close()
    assert c2 == "happy"


@ensureDeferred
async def test_get_code_early(reactor, mailbox):
    eq = EventualQueue(reactor)
    w1 = wormhole.create(APPID, mailbox.url, reactor, _eventual_queue=eq)
    d = w1.get_code()
    w1.set_code("1-abc")
    await eq.flush()
    code = await d
    assert code == "1-abc"
    with pytest.raises(LonelyError):
        await w1.close()


@ensureDeferred
async def test_get_code_late(reactor, mailbox):
    eq = EventualQueue(reactor)
    w1 = wormhole.create(APPID, mailbox.url, reactor, _eventual_queue=eq)
    w1.set_code("1-abc")
    d = w1.get_code()
    await eq.flush()
    code = await d
    assert code == "1-abc"
    with pytest.raises(LonelyError):
        await w1.close()


@ensureDeferred
async def test_same_message(reactor, mailbox):
    # the two sides use random nonces for their messages, so it's ok for
    # both to try and send the same body: they'll result in distinct
    # encrypted messages
    w1 = wormhole.create(APPID, mailbox.url, reactor)
    w2 = wormhole.create(APPID, mailbox.url, reactor)
    w1.allocate_code()
    code = await w1.get_code()
    w2.set_code(code)
    w1.send_message(b"data")
    w2.send_message(b"data")
    dataX = await w1.get_message()
    dataY = await w2.get_message()
    assert dataX == b"data"
    assert dataY == b"data"
    await w1.close()
    await w2.close()


@ensureDeferred
async def test_interleaved(reactor, mailbox):
    w1 = wormhole.create(APPID, mailbox.url, reactor)
    w2 = wormhole.create(APPID, mailbox.url, reactor)
    w1.allocate_code()
    code = await w1.get_code()
    w2.set_code(code)
    w1.send_message(b"data1")
    dataY = await w2.get_message()
    assert dataY == b"data1"
    d = w1.get_message()
    w2.send_message(b"data2")
    dataX = await d
    assert dataX == b"data2"
    await w1.close()
    await w2.close()


@ensureDeferred
async def test_unidirectional(reactor, mailbox):
    w1 = wormhole.create(APPID, mailbox.url, reactor)
    w2 = wormhole.create(APPID, mailbox.url, reactor)
    w1.allocate_code()
    code = await w1.get_code()
    w2.set_code(code)
    w1.send_message(b"data1")
    dataY = await w2.get_message()
    assert dataY == b"data1"
    await w1.close()
    await w2.close()


@ensureDeferred
async def test_early(reactor, mailbox):
    w1 = wormhole.create(APPID, mailbox.url, reactor)
    w1.send_message(b"data1")
    w2 = wormhole.create(APPID, mailbox.url, reactor)
    d = w2.get_message()
    w1.set_code("123-abc-def")
    w2.set_code("123-abc-def")
    dataY = await d
    assert dataY == b"data1"
    await w1.close()
    await w2.close()


@ensureDeferred
async def test_fixed_code(reactor, mailbox):
    w1 = wormhole.create(APPID, mailbox.url, reactor)
    w2 = wormhole.create(APPID, mailbox.url, reactor)
    w1.set_code("123-purple-elephant")
    w2.set_code("123-purple-elephant")
    w1.send_message(b"data1"), w2.send_message(b"data2")
    dl = await doBoth(w1.get_message(), w2.get_message())
    (dataX, dataY) = dl
    assert dataX == b"data2"
    assert dataY == b"data1"
    await w1.close()
    await w2.close()


@ensureDeferred
async def test_input_code(reactor, mailbox):
    w1 = wormhole.create(APPID, mailbox.url, reactor)
    w2 = wormhole.create(APPID, mailbox.url, reactor)
    w1.set_code("123-purple-elephant")
    h = w2.input_code()
    h.choose_nameplate("123")
    # Pause to allow some messages to get delivered. Specifically we want
    # to wait until w2 claims the nameplate, opens the mailbox, and
    # receives the PAKE message, to exercise the PAKE-before-CODE path in
    # Key.
    await poll_until(lambda: w2._boss._K._debug_pake_stashed)
    h.choose_words("purple-elephant")

    w1.send_message(b"data1"), w2.send_message(b"data2")
    dl = await doBoth(w1.get_message(), w2.get_message())
    (dataX, dataY) = dl
    assert dataX == b"data2"
    assert dataY == b"data1"
    await w1.close()
    await w2.close()


@ensureDeferred
async def test_multiple_messages(reactor, mailbox):
    w1 = wormhole.create(APPID, mailbox.url, reactor)
    w2 = wormhole.create(APPID, mailbox.url, reactor)
    w1.set_code("123-purple-elephant")
    w2.set_code("123-purple-elephant")
    w1.send_message(b"data1"), w2.send_message(b"data2")
    w1.send_message(b"data3"), w2.send_message(b"data4")
    dl = await doBoth(w1.get_message(), w2.get_message())
    (dataX, dataY) = dl
    assert dataX == b"data2"
    assert dataY == b"data1"
    dl = await doBoth(w1.get_message(), w2.get_message())
    (dataX, dataY) = dl
    assert dataX == b"data4"
    assert dataY == b"data3"
    await w1.close()
    await w2.close()


@ensureDeferred
async def test_closed(reactor, mailbox):
    eq = EventualQueue(reactor)
    w1 = wormhole.create(APPID, mailbox.url, reactor, _eventual_queue=eq)
    w2 = wormhole.create(APPID, mailbox.url, reactor, _eventual_queue=eq)
    w1.set_code("123-foo")
    w2.set_code("123-foo")

    # let it connect and become HAPPY
    await w1.get_versions()
    await w2.get_versions()

    await w1.close()
    await w2.close()

    # once closed, all Deferred-awaiting API calls get an prompt error
    with pytest.raises(WormholeClosed):
        await w1.get_welcome()
    with pytest.raises(WormholeClosed) as f:
        await w1.get_code()
    assert f.value.args[0] == "happy"
    with pytest.raises(WormholeClosed):
        await w1.get_unverified_key()
    with pytest.raises(WormholeClosed):
        await w1.get_verifier()
    with pytest.raises(WormholeClosed):
        await w1.get_versions()
    with pytest.raises(WormholeClosed):
        await w1.get_message()


@ensureDeferred
async def test_closed_idle(reactor):
    port = allocate_tcp_port()
    # without a relay server, this won't ever connect
    w1 = wormhole.create(APPID, f"ws://127.0.0.1:{port}/v1", reactor)

    d_welcome = w1.get_welcome()
    assert not d_welcome.called
    d_code = w1.get_code()
    d_key = w1.get_unverified_key()
    d_verifier = w1.get_verifier()
    d_versions = w1.get_versions()
    d_message = w1.get_message()

    with pytest.raises(LonelyError):
        await w1.close()

    with pytest.raises(LonelyError):
        await d_welcome
    with pytest.raises(LonelyError):
        await d_code
    with pytest.raises(LonelyError):
        await d_key
    with pytest.raises(LonelyError):
        await d_verifier
    with pytest.raises(LonelyError):
        await d_versions
    with pytest.raises(LonelyError):
        await d_message


@ensureDeferred
async def test_wrong_password(reactor, mailbox):
    eq = EventualQueue(reactor)
    w1 = wormhole.create(APPID, mailbox.url, reactor, _eventual_queue=eq)
    w2 = wormhole.create(APPID, mailbox.url, reactor, _eventual_queue=eq)
    w1.allocate_code()
    code = await w1.get_code()
    w2.set_code(code + "not")
    code2 = await w2.get_code()
    assert code != code2
    # That's enough to allow both sides to discover the mismatch, but
    # only after the confirmation message gets through. API calls that
    # don't wait will appear to work until the mismatched confirmation
    # message arrives.
    w1.send_message(b"should still work")
    w2.send_message(b"should still work")

    key2 = await w2.get_unverified_key()  # should work
    # w2 has just received w1.PAKE, and is about to send w2.VERSION
    key1 = await w1.get_unverified_key()  # should work
    # w1 has just received w2.PAKE, and is about to send w1.VERSION, and
    # then will receive w2.VERSION. When it sees w2.VERSION, it will
    # learn about the WrongPasswordError.
    assert key1 != key2

    # API calls that wait (i.e. get) will errback. We collect all these
    # Deferreds early to exercise the wait-then-fail path
    d1_verified = w1.get_verifier()
    d1_versions = w1.get_versions()
    d1_received = w1.get_message()
    d2_verified = w2.get_verifier()
    d2_versions = w2.get_versions()
    d2_received = w2.get_message()

    # wait for each side to notice the failure
    with pytest.raises(WrongPasswordError):
        await w1.get_verifier()
    with pytest.raises(WrongPasswordError):
        await w2.get_verifier()
    # the rest of the loops should fire within the next tick
    await eq.flush()

    # now all the rest should have fired already
    with pytest.raises(WrongPasswordError):
        await d1_verified
    with pytest.raises(WrongPasswordError):
        await d1_versions
    with pytest.raises(WrongPasswordError):
        await d1_received
    with pytest.raises(WrongPasswordError):
        await d2_verified
    with pytest.raises(WrongPasswordError):
        await d2_versions
    with pytest.raises(WrongPasswordError):
        await d2_received

    # and at this point, with the failure safely noticed by both sides,
    # new get_unverified_key() calls should signal the failure, even
    # before we close

    # any new calls in the error state should immediately fail
    with pytest.raises(WrongPasswordError):
        await w1.get_unverified_key()
    with pytest.raises(WrongPasswordError):
        await w1.get_verifier()
    with pytest.raises(WrongPasswordError):
        await w1.get_versions()
    with pytest.raises(WrongPasswordError):
        await w1.get_message()
    with pytest.raises(WrongPasswordError):
        await w2.get_unverified_key()
    with pytest.raises(WrongPasswordError):
        await w2.get_verifier()
    with pytest.raises(WrongPasswordError):
        await w2.get_versions()
    with pytest.raises(WrongPasswordError):
        await w2.get_message()

    with pytest.raises(WrongPasswordError):
        await w1.close()
    with pytest.raises(WrongPasswordError):
        await w2.close()

    # API calls should still get the error, not WormholeClosed
    with pytest.raises(WrongPasswordError):
        await w1.get_unverified_key()
    with pytest.raises(WrongPasswordError):
        await w1.get_verifier()
    with pytest.raises(WrongPasswordError):
        await w1.get_versions()
    with pytest.raises(WrongPasswordError):
        await w1.get_message()
    with pytest.raises(WrongPasswordError):
        await w2.get_unverified_key()
    with pytest.raises(WrongPasswordError):
        await w2.get_verifier()
    with pytest.raises(WrongPasswordError):
        await w2.get_versions()
    with pytest.raises(WrongPasswordError):
        await w2.get_message()


@ensureDeferred
async def test_wrong_password_with_spaces(reactor, mailbox):
    w = wormhole.create(APPID, mailbox.url, reactor)
    badcode = "4 oops spaces"
    with pytest.raises(KeyFormatError) as ex:
        w.set_code(badcode)
    expected_msg = f"Code '{badcode}' contains spaces."
    assert expected_msg == str(ex.value)
    with pytest.raises(LonelyError):
        await w.close()


@ensureDeferred
async def test_wrong_password_with_leading_space(reactor, mailbox):
    w = wormhole.create(APPID, mailbox.url, reactor)
    badcode = " 4-oops-space"
    with pytest.raises(KeyFormatError) as ex:
        w.set_code(badcode)
    expected_msg = f"Code '{badcode}' contains spaces."
    assert expected_msg == str(ex.value)
    with pytest.raises(LonelyError):
        await w.close()


@ensureDeferred
async def test_wrong_password_with_non_numeric_nameplate(reactor, mailbox):
    w = wormhole.create(APPID, mailbox.url, reactor)
    badcode = "four-oops-space"
    with pytest.raises(KeyFormatError) as ex:
        w.set_code(badcode)
    expected_msg = "Nameplate 'four' must be numeric, with no spaces."
    assert expected_msg == str(ex.value)
    with pytest.raises(LonelyError):
        await w.close()


@ensureDeferred
async def test_welcome(reactor, mailbox):
    w1 = wormhole.create(APPID, mailbox.url, reactor)
    wel1 = await w1.get_welcome()  # early: before connection established
    wel2 = await w1.get_welcome()  # late: already received welcome
    assert wel1 == wel2
    assert "current_cli_version" in wel1

    # cause an error, so a later get_welcome will return the error
    w1.set_code("123-foo")
    w2 = wormhole.create(APPID, mailbox.url, reactor)
    w2.set_code("123-NOT")
    with pytest.raises(WrongPasswordError):
        await w1.get_verifier()
    with pytest.raises(WrongPasswordError):
        await w1.get_welcome()

    # we have to ensure w2 receives a "bad" message from w1 before
    # the w2.close() assertion below will actually fail
    with pytest.raises(WrongPasswordError):
        await w2.get_verifier()

    with pytest.raises(WrongPasswordError):
        await w1.close()
    with pytest.raises(WrongPasswordError):
        await w2.close()


@ensureDeferred
async def test_verifier(reactor, mailbox):
    eq = EventualQueue(reactor)
    w1 = wormhole.create(APPID, mailbox.url, reactor, _eventual_queue=eq)
    w2 = wormhole.create(APPID, mailbox.url, reactor, _eventual_queue=eq)
    w1.allocate_code()
    code = await w1.get_code()
    w2.set_code(code)
    v1 = await w1.get_verifier()  # early
    v2 = await w2.get_verifier()
    assert type(v1) is bytes
    assert v1 == v2
    w1.send_message(b"data1")
    w2.send_message(b"data2")
    dataX = await w1.get_message()
    dataY = await w2.get_message()
    assert dataX == b"data2"
    assert dataY == b"data1"

    # calling get_verifier() this late should fire right away
    d = w2.get_verifier()
    await eq.flush()
    v1_late = await d
    assert v1_late == v1

    await w1.close()
    await w2.close()


@ensureDeferred
async def test_versions(reactor, mailbox):
    # there's no API for this yet, but make sure the internals work
    w1 = wormhole.create(
        APPID, mailbox.url, reactor, versions={"w1": 123})
    w2 = wormhole.create(
        APPID, mailbox.url, reactor, versions={"w2": 456})
    w1.allocate_code()
    code = await w1.get_code()
    w2.set_code(code)
    w1_versions = await w2.get_versions()
    assert w1_versions == {"w1": 123}
    w2_versions = await w1.get_versions()
    assert w2_versions == {"w2": 456}
    await w1.close()
    await w2.close()


@ensureDeferred
async def test_rx_dedup(reactor, mailbox):
    # Future clients will handle losing/reestablishing the Rendezvous
    # Server connection by retransmitting messages, which will sometimes
    # cause duplicate messages. Make sure this client can tolerate them.
    # The first place this would fail was when the second copy of the
    # incoming PAKE message was received, which would cause
    # SPAKE2.finish() to be called a second time, which throws an error
    # (which, being somewhat unexpected, caused a hang rather than a
    # clear exception). The Mailbox object is responsible for
    # deduplication, so we must patch the RendezvousConnector to simulate
    # duplicated messages.
    with mock.patch("wormhole._boss.RendezvousConnector", MessageDoubler):
        w1 = wormhole.create(APPID, mailbox.url, reactor)
    w2 = wormhole.create(APPID, mailbox.url, reactor)
    w1.set_code("123-purple-elephant")
    w2.set_code("123-purple-elephant")
    w1.send_message(b"data1"), w2.send_message(b"data2")
    dl = await doBoth(w1.get_message(), w2.get_message())
    (dataX, dataY) = dl
    assert dataX == b"data2"
    assert dataY == b"data1"
    await w1.close()
    await w2.close()


class MessageDoubler(_rendezvous.RendezvousConnector):
    # we could double messages on the sending side, but a future server will
    # strip those duplicates, so to really exercise the receiver, we must
    # double them on the inbound side instead
    # def _msg_send(self, phase, body):
    #     wormhole._Wormhole._msg_send(self, phase, body)
    #     self._ws_send_command("add", phase=phase, body=bytes_to_hexstr(body))
    def _response_handle_message(self, msg):
        _rendezvous.RendezvousConnector._response_handle_message(self, msg)
        _rendezvous.RendezvousConnector._response_handle_message(self, msg)


@ensureDeferred
async def test_derive_key_early(reactor, mailbox):
    w = wormhole.create(APPID, mailbox.url, reactor)
    # definitely too early
    with pytest.raises(NoKeyError):
        w.derive_key("purpose", 12)
    with pytest.raises(LonelyError):
        await w.close()


@ensureDeferred
async def test_multiple_set_code(reactor, mailbox):
    w = wormhole.create(APPID, mailbox.url, reactor)
    w.set_code("123-purple-elephant")
    # code can only be set once
    with pytest.raises(OnlyOneCodeError):
        w.set_code("123-nope")
    with pytest.raises(LonelyError):
        await w.close()


@ensureDeferred
async def test_allocate_and_set_code(reactor, mailbox):
    w = wormhole.create(APPID, mailbox.url, reactor)
    w.allocate_code()
    await w.get_code()
    with pytest.raises(OnlyOneCodeError):
        w.set_code("123-nope")
    with pytest.raises(LonelyError):
        await w.close()


@ensureDeferred
async def test_reconnection_basic(reactor, mailbox):
    w1 = wormhole.create(APPID, mailbox.url, reactor)
    w1_in = []
    w1._boss._RC._debug_record_inbound_f = w1_in.append
    # w1.debug_set_trace("W1")
    w1.allocate_code()
    code = await w1.get_code()
    w1.send_message(b"data1")  # queued until wormhole is established

    # now wait until we've deposited all our messages on the server
    def seen_our_pake():
        for m in w1_in:
            if m["type"] == "message" and m["phase"] == "pake":
                return True
        return False

    await poll_until(seen_our_pake)

    w1_in[:] = []
    # drop the connection
    w1._boss._RC._ws.transport.loseConnection()
    # wait for it to reconnect and redeliver all the messages. The server
    # sends mtype=message messages in random order, but we've only sent
    # one of them, so it's safe to wait for just the PAKE phase.
    await poll_until(seen_our_pake)

    # now let the second side proceed. this simulates the most common
    # case: the server is bounced while the sender is waiting, before the
    # receiver has started

    w2 = wormhole.create(APPID, mailbox.url, reactor)
    # w2.debug_set_trace("  W2")
    w2.set_code(code)

    dataY = await w2.get_message()
    assert dataY == b"data1"

    w2.send_message(b"data2")
    dataX = await w1.get_message()
    assert dataX == b"data2"

    c1 = await w1.close()
    assert c1 == "happy"
    c2 = await w2.close()
    assert c2 == "happy"


@ensureDeferred
async def assertSCEFailure(eq, d, innerType):
    await eq.flush()
    with pytest.raises(ServerConnectionError) as f:
        await d
    inner = f.value.reason
    assert isinstance(inner, innerType)
    return inner


@ensureDeferred
async def test_bad_dns(reactor):
    eq = EventualQueue(reactor)
    # point at a URL that will never connect
    w = wormhole.create(
        APPID, "ws://%%%.example.org:4000/v1", reactor, _eventual_queue=eq)
    # that should have already received an error, when it tried to
    # resolve the bogus DNS name. All API calls will return an error.

    e = await assertSCEFailure(eq, w.get_unverified_key(), ValueError)
    assert isinstance(e, ValueError)
    assert str(e) == "invalid hostname: %%%.example.org"
    await assertSCEFailure(eq, w.get_code(), ValueError)
    await assertSCEFailure(eq, w.get_verifier(), ValueError)
    await assertSCEFailure(eq, w.get_versions(), ValueError)
    await assertSCEFailure(eq, w.get_message(), ValueError)


@ensureDeferred
async def assertSCE(d, innerType):
    with pytest.raises(ServerConnectionError) as f:
        await d
    inner = f.value.reason
    assert isinstance(inner, innerType)
    return inner


@ensureDeferred
async def test_no_connection(reactor):
    # point at a URL that will never connect
    port = allocate_tcp_port()
    w = wormhole.create(APPID, f"ws://127.0.0.1:{port}/v1", reactor)
    # nothing is listening, but it will take a turn to discover that
    d1 = w.get_code()
    d2 = w.get_unverified_key()
    d3 = w.get_verifier()
    d4 = w.get_versions()
    d5 = w.get_message()
    await assertSCE(d1, ConnectionRefusedError)
    await assertSCE(d2, ConnectionRefusedError)
    await assertSCE(d3, ConnectionRefusedError)
    await assertSCE(d4, ConnectionRefusedError)
    await assertSCE(d5, ConnectionRefusedError)


@ensureDeferred
async def test_all_deferreds(reactor):
    # point at a URL that will never connect
    port = allocate_tcp_port()
    w = wormhole.create(APPID, f"ws://127.0.0.1:{port}/v1", reactor)
    # nothing is listening, but it will take a turn to discover that
    w.allocate_code()
    d1 = w.get_code()
    d2 = w.get_unverified_key()
    d3 = w.get_verifier()
    d4 = w.get_versions()
    d5 = w.get_message()
    await assertSCE(d1, ConnectionRefusedError)
    await assertSCE(d2, ConnectionRefusedError)
    await assertSCE(d3, ConnectionRefusedError)
    await assertSCE(d4, ConnectionRefusedError)
    await assertSCE(d5, ConnectionRefusedError)


def test_trace_basic(reactor):
    w1 = wormhole.create(APPID, "ws://localhost:1", reactor)
    stderr = io.StringIO()
    w1.debug_set_trace("W1", file=stderr)
    # if Automat doesn't have the tracing API, then we won't actually
    # exercise the tracing function, so exercise the RendezvousConnector
    # function manually (it isn't a state machine, so it will always wire
    # up the tracer)
    w1._boss._RC._debug("what")

    stderr = io.StringIO()
    out = w1._boss._print_trace("OLD", "IN", "NEW", "C1", "M1", stderr)
    assert stderr.getvalue().splitlines() == \
                     ["C1.M1[OLD].IN -> [NEW]"]
    out("OUT1")
    assert stderr.getvalue().splitlines() == \
                     ["C1.M1[OLD].IN -> [NEW]", " C1.M1.OUT1()"]
    w1._boss._print_trace("", "R.connected", "", "C1", "RC1", stderr)
    assert stderr.getvalue().splitlines() == \
        ["C1.M1[OLD].IN -> [NEW]", " C1.M1.OUT1()", "C1.RC1.R.connected"]


def test_trace_delegated(reactor):
    dg = Delegate()
    w1 = wormhole.create(APPID, "ws://localhost:1", reactor, delegate=dg)
    stderr = io.StringIO()
    w1.debug_set_trace("W1", file=stderr)
    w1._boss._RC._debug("what")
