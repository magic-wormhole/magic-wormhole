import errno
import os
import re
import subprocess
from unittest import mock

from .. import ipaddrs

DOTTED_QUAD_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$")

MOCK_IPADDR_OUTPUT = """\
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 16436 qdisc noqueue state UNKNOWN \n\
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    inet 127.0.0.1/8 scope host lo
    inet6 ::1/128 scope host \n\
       valid_lft forever preferred_lft forever
2: eth1: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc pfifo_fast state UP \
qlen 1000
    link/ether d4:3d:7e:01:b4:3e brd ff:ff:ff:ff:ff:ff
    inet 192.168.0.6/24 brd 192.168.0.255 scope global eth1
    inet6 fe80::d63d:7eff:fe01:b43e/64 scope link \n\
       valid_lft forever preferred_lft forever
3: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc mq state UP qlen\
 1000
    link/ether 90:f6:52:27:15:0a brd ff:ff:ff:ff:ff:ff
    inet 192.168.0.2/24 brd 192.168.0.255 scope global wlan0
    inet6 fe80::92f6:52ff:fe27:150a/64 scope link \n\
       valid_lft forever preferred_lft forever
"""

MOCK_IFCONFIG_OUTPUT = """\
eth1      Link encap:Ethernet  HWaddr d4:3d:7e:01:b4:3e  \n\
          inet addr:192.168.0.6  Bcast:192.168.0.255  Mask:255.255.255.0
          inet6 addr: fe80::d63d:7eff:fe01:b43e/64 Scope:Link
          UP BROADCAST RUNNING MULTICAST  MTU:1500  Metric:1
          RX packets:154242234 errors:0 dropped:0 overruns:0 frame:0
          TX packets:155461891 errors:0 dropped:0 overruns:0 carrier:0
          collisions:0 txqueuelen:1000 \n\
          RX bytes:84367213640 (78.5 GiB)  TX bytes:73401695329 (68.3 GiB)
          Interrupt:20 Memory:f4f00000-f4f20000 \n\

lo        Link encap:Local Loopback  \n\
          inet addr:127.0.0.1  Mask:255.0.0.0
          inet6 addr: ::1/128 Scope:Host
          UP LOOPBACK RUNNING  MTU:16436  Metric:1
          RX packets:27449267 errors:0 dropped:0 overruns:0 frame:0
          TX packets:27449267 errors:0 dropped:0 overruns:0 carrier:0
          collisions:0 txqueuelen:0 \n\
          RX bytes:192643017823 (179.4 GiB)  TX bytes:192643017823 (179.4 GiB)

wlan0     Link encap:Ethernet  HWaddr 90:f6:52:27:15:0a  \n\
          inet addr:192.168.0.2  Bcast:192.168.0.255  Mask:255.255.255.0
          inet6 addr: fe80::92f6:52ff:fe27:150a/64 Scope:Link
          UP BROADCAST RUNNING MULTICAST  MTU:1500  Metric:1
          RX packets:12352750 errors:0 dropped:0 overruns:0 frame:0
          TX packets:4501451 errors:0 dropped:0 overruns:0 carrier:0
          collisions:0 txqueuelen:1000 \n\
          RX bytes:3916475942 (3.6 GiB)  TX bytes:458353654 (437.1 MiB)
"""

# This is actually from a VirtualBox VM running XP.
MOCK_ROUTE_OUTPUT = """\
===========================================================================
Interface List
0x1 ........................... MS TCP Loopback interface
0x2 ...08 00 27 c3 80 ad ...... AMD PCNET Family PCI Ethernet Adapter - \
Packet Scheduler Miniport
===========================================================================
===========================================================================
Active Routes:
Network Destination        Netmask          Gateway       Interface  Metric
          0.0.0.0          0.0.0.0         10.0.2.2       10.0.2.15       20
         10.0.2.0    255.255.255.0        10.0.2.15       10.0.2.15       20
        10.0.2.15  255.255.255.255        127.0.0.1       127.0.0.1       20
   10.255.255.255  255.255.255.255        10.0.2.15       10.0.2.15       20
        127.0.0.0        255.0.0.0        127.0.0.1       127.0.0.1       1
        224.0.0.0        240.0.0.0        10.0.2.15       10.0.2.15       20
  255.255.255.255  255.255.255.255        10.0.2.15       10.0.2.15       1
Default Gateway:          10.0.2.2
===========================================================================
Persistent Routes:
  None
"""

UNIX_TEST_ADDRESSES = {"127.0.0.1", "192.168.0.6", "192.168.0.2"}
WINDOWS_TEST_ADDRESSES = {"127.0.0.1", "10.0.2.15"}
CYGWIN_TEST_ADDRESSES = {"127.0.0.1"}


class FakeProcess:
    def __init__(self, output, err):
        self.output = output
        self.err = err

    def communicate(self):
        return (self.output, self.err)


def test_list():
    addresses = ipaddrs.find_addresses()
    assert "127.0.0.1" in addresses
    assert "0.0.0.0" not in addresses

# David A.'s OpenSolaris box timed out on this test one time when it was at
# 2s.
test_list.timeout = 4

def _test_list_mock(command, output, expected):
    first = True

    def call_Popen(args,
                   bufsize=0,
                   executable=None,
                   stdin=None,
                   stdout=None,
                   stderr=None,
                   preexec_fn=None,
                   close_fds=False,
                   shell=False,
                   cwd=None,
                   env=None,
                   universal_newlines=False,
                   startupinfo=None,
                   creationflags=0):
        nonlocal first
        if first:
            first = False
            e = OSError("EINTR")
            e.errno = errno.EINTR
            raise e
        elif os.path.basename(args[0]) == command:
            return FakeProcess(output, "")
        else:
            e = OSError("[Errno 2] No such file or directory")
            e.errno = errno.ENOENT
            raise e

    def call_which(name):
        return [name]

    patch_popen = mock.patch.object(subprocess, 'Popen', call_Popen)
    patch_isfile = mock.patch.object(os.path, 'isfile', lambda x: True)
    patch_which = mock.patch.object(ipaddrs, 'which', call_which)

    with patch_popen, patch_isfile, patch_which:
        addresses = ipaddrs.find_addresses()

    assert set(addresses) == set(expected)

def test_list_mock_ip_addr():
    with mock.patch.object(ipaddrs, 'platform', "linux2"):
        _test_list_mock("ip", MOCK_IPADDR_OUTPUT, UNIX_TEST_ADDRESSES)

def test_list_mock_ifconfig():
    with mock.patch.object(ipaddrs, 'platform', "linux2"):
        _test_list_mock("ifconfig", MOCK_IFCONFIG_OUTPUT,
                        UNIX_TEST_ADDRESSES)

def test_list_mock_route():
    with mock.patch.object(ipaddrs, 'platform', "win32"):
        _test_list_mock("route.exe", MOCK_ROUTE_OUTPUT,
                        WINDOWS_TEST_ADDRESSES)

def test_list_mock_cygwin():
    with mock.patch.object(ipaddrs, 'platform', "cygwin"):
        _test_list_mock(None, None, CYGWIN_TEST_ADDRESSES)
