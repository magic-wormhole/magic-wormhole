from __future__ import print_function, unicode_literals
import io
from collections import namedtuple
from unittest import mock
from twisted.internet import endpoints, reactor
from twisted.trial import unittest
from .._hints import (endpoint_from_hint_obj, parse_hint_argv, parse_tcp_v1_hint,
                      describe_hint_obj, parse_hint, encode_hint,
                      DirectTCPV1Hint, TorTCPV1Hint, RelayV1Hint)

UnknownHint = namedtuple("UnknownHint", ["stuff"])


class Hints(unittest.TestCase):
    def test_endpoint_from_hint_obj(self):
        def efho(hint, tor=None):
            return endpoint_from_hint_obj(hint, tor, reactor)
        self.assertIsInstance(efho(DirectTCPV1Hint("host", 1234, 0.0)),
                              endpoints.HostnameEndpoint)
        self.assertEqual(efho("unknown:stuff:yowza:pivlor"), None)

        # tor=None
        self.assertEqual(efho(TorTCPV1Hint("host", "port", 0)), None)
        self.assertEqual(efho(UnknownHint("foo")), None)

        tor = mock.Mock()

        def tor_ep(hostname, port):
            if hostname == "non-public":
                raise ValueError
            return ("tor_ep", hostname, port)
        tor.stream_via = mock.Mock(side_effect=tor_ep)

        self.assertEqual(efho(DirectTCPV1Hint("host", 1234, 0.0), tor),
                         ("tor_ep", "host", 1234))
        self.assertEqual(efho(TorTCPV1Hint("host2.onion", 1234, 0.0), tor),
                         ("tor_ep", "host2.onion", 1234))
        self.assertEqual(efho(DirectTCPV1Hint("non-public", 1234, 0.0), tor), None)

        self.assertEqual(efho(UnknownHint("foo"), tor), None)

    def test_comparable(self):
        h1 = DirectTCPV1Hint("hostname", "port1", 0.0)
        h1b = DirectTCPV1Hint("hostname", "port1", 0.0)
        h2 = DirectTCPV1Hint("hostname", "port2", 0.0)
        r1 = RelayV1Hint(tuple(sorted([h1, h2])))
        r2 = RelayV1Hint(tuple(sorted([h2, h1])))
        r3 = RelayV1Hint(tuple(sorted([h1b, h2])))
        self.assertEqual(r1, r2)
        self.assertEqual(r2, r3)
        self.assertEqual(len(set([r1, r2, r3])), 1)

    def test_parse_tcp_v1_hint(self):
        p = parse_tcp_v1_hint
        self.assertEqual(p({"type": "unknown"}), None)
        h = p({"type": "direct-tcp-v1", "hostname": "foo", "port": 1234})
        self.assertEqual(h, DirectTCPV1Hint("foo", 1234, 0.0))
        h = p({
            "type": "direct-tcp-v1",
            "hostname": "foo",
            "port": 1234,
            "priority": 2.5
        })
        self.assertEqual(h, DirectTCPV1Hint("foo", 1234, 2.5))
        h = p({"type": "tor-tcp-v1", "hostname": "foo", "port": 1234})
        self.assertEqual(h, TorTCPV1Hint("foo", 1234, 0.0))
        h = p({
            "type": "tor-tcp-v1",
            "hostname": "foo",
            "port": 1234,
            "priority": 2.5
        })
        self.assertEqual(h, TorTCPV1Hint("foo", 1234, 2.5))
        self.assertEqual(p({
            "type": "direct-tcp-v1"
        }), None)  # missing hostname
        self.assertEqual(p({
            "type": "direct-tcp-v1",
            "hostname": 12
        }), None)  # invalid hostname
        self.assertEqual(
            p({
                "type": "direct-tcp-v1",
                "hostname": "foo"
            }), None)  # missing port
        self.assertEqual(
            p({
                "type": "direct-tcp-v1",
                "hostname": "foo",
                "port": "not a number"
            }), None)  # invalid port

    def test_parse_hint(self):
        p = parse_hint
        self.assertEqual(p({"type": "direct-tcp-v1",
                            "hostname": "foo",
                            "port": 12}),
                         DirectTCPV1Hint("foo", 12, 0.0))
        self.assertEqual(p({"type": "relay-v1",
                            "hints": [
                                {"type": "direct-tcp-v1",
                                 "hostname": "foo",
                                 "port": 12},
                                {"type": "unrecognized"},
                                {"type": "direct-tcp-v1",
                                 "hostname": "bar",
                                 "port": 13}]}),
                         RelayV1Hint([DirectTCPV1Hint("foo", 12, 0.0),
                                      DirectTCPV1Hint("bar", 13, 0.0)]))

    def test_parse_hint_argv(self):
        def p(hint):
            stderr = io.StringIO()
            value = parse_hint_argv(hint, stderr=stderr)
            return value, stderr.getvalue()

        h, stderr = p("tcp:host:1234")
        self.assertEqual(h, DirectTCPV1Hint("host", 1234, 0.0))
        self.assertEqual(stderr, "")

        h, stderr = p("tcp:host:1234:priority=2.6")
        self.assertEqual(h, DirectTCPV1Hint("host", 1234, 2.6))
        self.assertEqual(stderr, "")

        h, stderr = p("tcp:host:1234:unknown=stuff")
        self.assertEqual(h, DirectTCPV1Hint("host", 1234, 0.0))
        self.assertEqual(stderr, "")

        h, stderr = p("$!@#^")
        self.assertEqual(h, None)
        self.assertEqual(stderr, "unparseable hint '$!@#^'\n")

        h, stderr = p("unknown:stuff")
        self.assertEqual(h, None)
        self.assertEqual(stderr,
                         "unknown hint type 'unknown' in 'unknown:stuff'\n")

        h, stderr = p("tcp:just-a-hostname")
        self.assertEqual(h, None)
        self.assertEqual(
            stderr,
            "unparseable TCP hint (need more colons) 'tcp:just-a-hostname'\n")

        h, stderr = p("tcp:host:number")
        self.assertEqual(h, None)
        self.assertEqual(stderr,
                         "non-numeric port in TCP hint 'tcp:host:number'\n")

        h, stderr = p("tcp:host:1234:priority=bad")
        self.assertEqual(h, None)
        self.assertEqual(
            stderr,
            "non-float priority= in TCP hint 'tcp:host:1234:priority=bad'\n")

        h, stderr = p("tcp:[2001:0db8:85a3::8a2e:0370:7334]")
        self.assertEqual(h, None)
        self.assertEqual(
            stderr, "non-numeric port in TCP hint 'tcp:[2001:0db8:85a3::8a2e:0370:7334]'\n")

        h, stderr = p("tcp:[2001:0db8:85a3::8a2e:0370:7334]:1234")
        self.assertEqual(h, DirectTCPV1Hint(
            "2001:0db8:85a3::8a2e:0370:7334", 1234, 0.0))
        self.assertEqual(stderr, "")

        h, stderr = p("tcp:[2001:0db8:85a3::8a2e:0370:7334]:1234:priority=2.6")
        self.assertEqual(h, DirectTCPV1Hint(
            "2001:0db8:85a3::8a2e:0370:7334", 1234, 2.6))
        self.assertEqual(stderr, "")

        h, stderr = p("tcp:[abc::xyz]:1234")
        self.assertEqual(h, None)
        self.assertEqual(
            stderr, "invalid IPv6 address in TCP hint 'tcp:[abc::xyz]:1234'\n")

    def test_describe_hint_obj(self):
        d = describe_hint_obj
        self.assertEqual(d(DirectTCPV1Hint("host", 1234, 0.0), False, False),
                         "->tcp:host:1234")
        self.assertEqual(d(DirectTCPV1Hint("host", 1234, 0.0), True, False),
                         "->relay:tcp:host:1234")
        self.assertEqual(d(DirectTCPV1Hint("host", 1234, 0.0), False, True),
                         "tor->tcp:host:1234")
        self.assertEqual(d(DirectTCPV1Hint("host", 1234, 0.0), True, True),
                         "tor->relay:tcp:host:1234")
        self.assertEqual(d(TorTCPV1Hint("host", 1234, 0.0), False, False),
                         "->tor:host:1234")
        self.assertEqual(d(UnknownHint("stuff"), False, False),
                         "->%s" % str(UnknownHint("stuff")))

    def test_encode_hint(self):
        e = encode_hint
        self.assertEqual(e(DirectTCPV1Hint("host", 1234, 1.0)),
                         {"type": "direct-tcp-v1",
                          "priority": 1.0,
                          "hostname": "host",
                          "port": 1234})
        self.assertEqual(e(RelayV1Hint([DirectTCPV1Hint("foo", 12, 0.0),
                                        DirectTCPV1Hint("bar", 13, 0.0)])),
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
                          ]})
        self.assertEqual(e(TorTCPV1Hint("host", 1234, 1.0)),
                         {"type": "tor-tcp-v1",
                          "priority": 1.0,
                          "hostname": "host",
                          "port": 1234})
        e = self.assertRaises(ValueError, e, "not a Hint")
        self.assertIn("unknown hint type", str(e))
        self.assertIn("not a Hint", str(e))
