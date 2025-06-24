import io
from collections import namedtuple
from unittest import mock
from twisted.internet import endpoints, reactor
from .._hints import (endpoint_from_hint_obj, parse_hint_argv, parse_tcp_v1_hint,
                      describe_hint_obj, parse_hint, encode_hint,
                      DirectTCPV1Hint, TorTCPV1Hint, RelayV1Hint)
import pytest

UnknownHint = namedtuple("UnknownHint", ["stuff"])


def test_endpoint_from_hint_obj():
    def efho(hint, tor=None):
        return endpoint_from_hint_obj(hint, tor, reactor)
    assert isinstance(efho(DirectTCPV1Hint("host", 1234, 0.0)), endpoints.HostnameEndpoint)
    assert efho("unknown:stuff:yowza:pivlor") is None

    # tor=None
    assert efho(TorTCPV1Hint("host", "port", 0)) is None
    assert efho(UnknownHint("foo")) is None

    tor = mock.Mock()

    def tor_ep(hostname, port):
        if hostname == "non-public":
            raise ValueError
        return ("tor_ep", hostname, port)
    tor.stream_via = mock.Mock(side_effect=tor_ep)

    assert efho(DirectTCPV1Hint("host", 1234, 0.0), tor) == \
                     ("tor_ep", "host", 1234)
    assert efho(TorTCPV1Hint("host2.onion", 1234, 0.0), tor) == \
                     ("tor_ep", "host2.onion", 1234)
    assert efho(DirectTCPV1Hint("non-public", 1234, 0.0), tor) is None

    assert efho(UnknownHint("foo"), tor) is None

def test_comparable():
    h1 = DirectTCPV1Hint("hostname", "port1", 0.0)
    h1b = DirectTCPV1Hint("hostname", "port1", 0.0)
    h2 = DirectTCPV1Hint("hostname", "port2", 0.0)
    r1 = RelayV1Hint(tuple(sorted([h1, h2])))
    r2 = RelayV1Hint(tuple(sorted([h2, h1])))
    r3 = RelayV1Hint(tuple(sorted([h1b, h2])))
    assert r1 == r2
    assert r2 == r3
    assert len(set([r1, r2, r3])) == 1

def test_parse_tcp_v1_hint():
    p = parse_tcp_v1_hint
    assert p({"type": "unknown"}) is None
    h = p({"type": "direct-tcp-v1", "hostname": "foo", "port": 1234})
    assert h == DirectTCPV1Hint("foo", 1234, 0.0)
    h = p({
        "type": "direct-tcp-v1",
        "hostname": "foo",
        "port": 1234,
        "priority": 2.5
    })
    assert h == DirectTCPV1Hint("foo", 1234, 2.5)
    h = p({"type": "tor-tcp-v1", "hostname": "foo", "port": 1234})
    assert h == TorTCPV1Hint("foo", 1234, 0.0)
    h = p({
        "type": "tor-tcp-v1",
        "hostname": "foo",
        "port": 1234,
        "priority": 2.5
    })
    assert h == TorTCPV1Hint("foo", 1234, 2.5)
    assert p({
        "type": "direct-tcp-v1"
    }) is None  # missing hostname
    assert p({
        "type": "direct-tcp-v1",
        "hostname": 12
    }) is None  # invalid hostname
    assert p({
            "type": "direct-tcp-v1",
            "hostname": "foo"
        }) is None  # missing port
    assert p({
            "type": "direct-tcp-v1",
            "hostname": "foo",
            "port": "not a number"
        }) is None  # invalid port

def test_parse_hint():
    p = parse_hint
    assert p({"type": "direct-tcp-v1",
                        "hostname": "foo",
                        "port": 12}) == \
                     DirectTCPV1Hint("foo", 12, 0.0)
    assert p({"type": "relay-v1",
                        "hints": [
                            {"type": "direct-tcp-v1",
                             "hostname": "foo",
                             "port": 12},
                            {"type": "unrecognized"},
                            {"type": "direct-tcp-v1",
                             "hostname": "bar",
                             "port": 13}]}) == \
                     RelayV1Hint([DirectTCPV1Hint("foo", 12, 0.0),
                                  DirectTCPV1Hint("bar", 13, 0.0)])

def test_parse_hint_argv():
    def p(hint):
        stderr = io.StringIO()
        value = parse_hint_argv(hint, stderr=stderr)
        return value, stderr.getvalue()

    h, stderr = p("tcp:host:1234")
    assert h == DirectTCPV1Hint("host", 1234, 0.0)
    assert stderr == ""

    h, stderr = p("tcp:host:1234:priority=2.6")
    assert h == DirectTCPV1Hint("host", 1234, 2.6)
    assert stderr == ""

    h, stderr = p("tcp:host:1234:unknown=stuff")
    assert h == DirectTCPV1Hint("host", 1234, 0.0)
    assert stderr == ""

    h, stderr = p("$!@#^")
    assert h is None
    assert stderr == "unparseable hint '$!@#^'\n"

    h, stderr = p("unknown:stuff")
    assert h is None
    assert stderr == \
                     "unknown hint type 'unknown' in 'unknown:stuff'\n"

    h, stderr = p("tcp:just-a-hostname")
    assert h is None
    assert stderr == \
        "unparseable TCP hint (need more colons) 'tcp:just-a-hostname'\n"

    h, stderr = p("tcp:host:number")
    assert h is None
    assert stderr == \
                     "non-numeric port in TCP hint 'tcp:host:number'\n"

    h, stderr = p("tcp:host:1234:priority=bad")
    assert h is None
    assert stderr == \
        "non-float priority= in TCP hint 'tcp:host:1234:priority=bad'\n"

    h, stderr = p("tcp:[2001:0db8:85a3::8a2e:0370:7334]")
    assert h is None
    assert stderr == "non-numeric port in TCP hint 'tcp:[2001:0db8:85a3::8a2e:0370:7334]'\n"

    h, stderr = p("tcp:[2001:0db8:85a3::8a2e:0370:7334]:1234")
    assert h == DirectTCPV1Hint(
        "2001:0db8:85a3::8a2e:0370:7334", 1234, 0.0)
    assert stderr == ""

    h, stderr = p("tcp:[2001:0db8:85a3::8a2e:0370:7334]:1234:priority=2.6")
    assert h == DirectTCPV1Hint(
        "2001:0db8:85a3::8a2e:0370:7334", 1234, 2.6)
    assert stderr == ""

    h, stderr = p("tcp:[abc::xyz]:1234")
    assert h is None
    assert stderr == "invalid IPv6 address in TCP hint 'tcp:[abc::xyz]:1234'\n"

def test_describe_hint_obj():
    d = describe_hint_obj
    assert d(DirectTCPV1Hint("host", 1234, 0.0), False, False) == \
                     "->tcp:host:1234"
    assert d(DirectTCPV1Hint("host", 1234, 0.0), True, False) == \
                     "->relay:tcp:host:1234"
    assert d(DirectTCPV1Hint("host", 1234, 0.0), False, True) == \
                     "tor->tcp:host:1234"
    assert d(DirectTCPV1Hint("host", 1234, 0.0), True, True) == \
                     "tor->relay:tcp:host:1234"
    assert d(TorTCPV1Hint("host", 1234, 0.0), False, False) == \
                     "->tor:host:1234"
    assert d(UnknownHint("stuff"), False, False) == \
                     f"->{str(UnknownHint('stuff'))}"

def test_encode_hint():
    e = encode_hint
    assert e(DirectTCPV1Hint("host", 1234, 1.0)) == \
                     {"type": "direct-tcp-v1",
                      "priority": 1.0,
                      "hostname": "host",
                      "port": 1234}
    assert e(RelayV1Hint([DirectTCPV1Hint("foo", 12, 0.0),
                                    DirectTCPV1Hint("bar", 13, 0.0)])) == \
                     {"type": "relay-v1",
                      "hints": [
                          {"type": "direct-tcp-v1",
                           "hostname": "foo",
                           "port": 12,
                           "priority": 0.0},
                          {"type": "direct-tcp-v1",
                           "hostname": "bar",
                           "port": 13,
                           "priority": 0.0},
                      ]}
    assert e(TorTCPV1Hint("host", 1234, 1.0)) == \
                     {"type": "tor-tcp-v1",
                      "priority": 1.0,
                      "hostname": "host",
                      "port": 1234}
    with pytest.raises(ValueError) as f:
        e("not a Hint")
    assert "unknown hint type" in str(f.value)
    assert "not a Hint" in str(f.value)
