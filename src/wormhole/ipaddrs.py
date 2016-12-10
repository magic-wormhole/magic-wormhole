# no unicode_literals
# Find all of our ip addresses. From tahoe's src/allmydata/util/iputil.py

import os, re, subprocess, errno
from sys import platform
from twisted.python.procutils import which

# Wow, I'm really amazed at home much mileage we've gotten out of calling
# the external route.exe program on windows...  It appears to work on all
# versions so far.  Still, the real system calls would much be preferred...
# ... thus wrote Greg Smith in time immemorial...
_win32_re = re.compile(r'^\s*\d+\.\d+\.\d+\.\d+\s.+\s(?P<address>\d+\.\d+\.\d+\.\d+)\s+(?P<metric>\d+)\s*$', flags=re.M|re.I|re.S)
_win32_commands = (('route.exe', ('print',), _win32_re),)

# These work in most Unices.
_addr_re = re.compile(r'^\s*inet [a-zA-Z]*:?(?P<address>\d+\.\d+\.\d+\.\d+)[\s/].+$', flags=re.M|re.I|re.S)
_unix_commands = (('/bin/ip', ('addr',), _addr_re),
                  ('/sbin/ip', ('addr',), _addr_re),
                  ('/sbin/ifconfig', ('-a',), _addr_re),
                  ('/usr/sbin/ifconfig', ('-a',), _addr_re),
                  ('/usr/etc/ifconfig', ('-a',), _addr_re),
                  ('ifconfig', ('-a',), _addr_re),
                  ('/sbin/ifconfig', (), _addr_re),
                 )


def find_addresses():
    # originally by Greg Smith, hacked by Zooko and then Daira

    # We don't reach here for cygwin.
    if platform == 'win32':
        commands = _win32_commands
    else:
        commands = _unix_commands

    for (pathtotool, args, regex) in commands:
        # If pathtotool is a fully qualified path then we just try that.
        # If it is merely an executable name then we use Twisted's
        # "which()" utility and try each executable in turn until one
        # gives us something that resembles a dotted-quad IPv4 address.

        if os.path.isabs(pathtotool):
            exes_to_try = [pathtotool]
        else:
            exes_to_try = which(pathtotool)

        for exe in exes_to_try:
            try:
                addresses = _query(exe, args, regex)
            except Exception:
                addresses = []
            if addresses:
                return addresses

    return ["127.0.0.1"]

def _query(path, args, regex):
    env = {'LANG': 'en_US.UTF-8'}
    TRIES = 5
    for trial in range(TRIES):
        try:
            p = subprocess.Popen([path] + list(args),
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 env=env,
                                 universal_newlines=True)
            (output, err) = p.communicate()
            break
        except OSError as e:
            if e.errno == errno.EINTR and trial < TRIES-1:
                continue
            raise

    addresses = []
    outputsplit = output.split('\n')
    for outline in outputsplit:
        m = regex.match(outline)
        if m:
            addr = m.group('address')
            if addr not in addresses:
                addresses.append(addr)

    return addresses
