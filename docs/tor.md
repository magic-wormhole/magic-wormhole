# Tor Support in Magic-Wormhole

The ``wormhole`` command-line tool has built-in support for performing
transfers over Tor. To use it, you must install with the "tor" extra,
like this:

```
pip install magic-wormhole[tor]
```

Unfortunately, at present, Tor support is only available under
python2.7. Attempting to install `magic-wormhole[tor]` (or `txtorcon` or
`txsocksx`) under py3 results in an inscrutable error that references
strings like "vcversioner", "install_requires must be a string or list
of strings", and "int object not iterable".
[Support for py3](https://github.com/warner/magic-wormhole/issues/136)
will hopefully be added in a future release.

## Usage

Under python2.7, just add ``--tor`` to use a running Tor daemon:

```
wormhole send --tor myfile.jpg
 
wormhole receive --tor
```

You should use ``--tor`` rather than running ``wormhole`` under tsocks
or torsocks because the magic-wormhole "Transit" protocol normally sends
the IP addresses of each computer to its peer, to attempt a direct
connection between the two (somewhat like the FTP protocol would do).
External tor-ifying programs don't know about this, so they can't strip
these addresses out. Using ``--tor`` puts magic-wormhole into a mode
where it does not share any IP addresses.

``--tor`` causes the program to look for a Tor control port in the three
most common locations:

* ``unix:/var/run/tor/control``: Debian/Ubuntu Tor listen here
* ``tcp:localhost:9051``: the standard Tor control port
* ``tcp:localhost:9151``: control port for TorBrowser's embedded Tor

If ``wormhole`` is unable to establish a control-port connection to any
of those locations, it will assume there is a SOCKS daemon listening on
``tcp:localhost:9050``, and hope for the best (if no SOCKS daemon is
available on that port, the initial Rendezvous connection will fail, and
the program will exit with an error before doing anything else).

The default behavior will Just Work if:

* you are on a Debian-like system, and the ``tor`` package is installed,
  or:
* you have launched the ``tor`` daemon manually, or:
* the TorBrowser application is running when you start ``wormhole``

On Debian-like systems, if your account is a member of the
``debian-tor`` group, ``wormhole`` will use the control-port to ask for
the right SOCKS port. If not, it should fall back to using the default
SOCKS port on 9050. To add your account to the ``debian-tor`` group, use
e.g. ``sudo adduser MYUSER debian-tor``. Access to the control-port will
be more significant in the future, when ``wormhole`` can listen on
"onion services": see below for details.

## Other Ways To Reach Tor

If ``tor`` is installed, but you cannot use the control-port or
SOCKS-port for some reason, then you can use ``--launch-tor`` to ask
``wormhole`` to start a new Tor daemon for the duration of the transfer
(and then shut it down afterwards). This will add 30-40 seconds to
program startup.

```
wormhole send --tor --launch-tor myfile.jpg
```

Alternatively, if you know of a pre-existing Tor daemon with a
non-standard control-port, you can specify that control port with the
``--tor-control-port=`` argument:

```
wormhole send --tor --tor-control-port=tcp:127.0.0.1:9251 myfile.jpg
```

## .onion servers

In the future, ``wormhole`` with ``--tor`` will listen on an ephemeral
"onion service" when file transfers are requested. If both sides are
Tor-capable, this will allow transfers to take place "directly" (via the
Tor network) from sender to receiver, bypassing the Transit Relay
server. This will require access to a Tor control-port (to ask Tor to
create a new ephemeral onion service). SOCKS-port access will not be
sufficient.

However the current version of ``wormhole`` does not use onion services.
For now, if both sides use ``--tor``, any file transfers must use the
transit relay, since neither side will advertise any listening IP
addresses.
