# Magic Wormhole
[![Build Status](https://travis-ci.org/warner/magic-wormhole.svg?branch=master)](https://travis-ci.org/warner/magic-wormhole)
[![codecov.io](https://codecov.io/github/warner/magic-wormhole/coverage.svg?branch=master)](https://codecov.io/github/warner/magic-wormhole?branch=master)

Get things from one computer to another, safely.

This package provides a library and a command-line tool named `wormhole`,
which makes it possible to get short pieces of text (and arbitrary-sized
files and directories) from one computer to another. The two endpoints are
identified by using identical "wormhole codes": in general, the sending
machine generates and displays the code, which must then be typed into the
receiving machine.

The codes are short and human-pronounceable, using a phonetically-distinct
wordlist. The receiving side offers tab-completion on the codewords, so
usually only a few characters must be typed. Wormhole codes are single-use
and do not need to be memorized.

## Installation

```$ pip install magic-wormhole```

On Debian/Ubuntu systems, you may first need `apt-get python-dev libffi-dev`.
On OS-X, you may need to install `pip`.

Developers can clone the source tree and run `tox` to run the unit tests on
all supported (and installed) versions of python: 2.7, 3.3, 3.4, and 3.5.

## Motivation

* Moving a file to a friend's machine, when the humans can speak to each
  other (directly) but the computers cannot
* Delivering a properly-random password to a new user via the phone
* Supplying an SSH public key for future login use

Copying files onto a USB stick requires physical proximity, and is
uncomfortable for transferring long-term secrets because flash memory is hard
to erase. Copying files with ssh/scp is fine, but requires previous
arrangements and an account on the target machine, and how do you bootstrap
the account? Copying files through email first requires transcribing an email
address in the opposite direction, and is even worse for secrets, because
email is unencrypted. Copying files through encrypted email requires
bootstrapping a GPG key as well as an email address. Copying files through
Dropbox is not secure against the Dropbox server and results in a large URL
that must be transcribed. Using a URL shortener adds an extra step, reveals
the full URL to the shortening service, and leaves a short URL that can be
guessed by outsiders.

Many common use cases start with a human-mediated communication channel, such
as IRC, IM, email, a phone call, or a face-to-face conversation. Some of
these are basically secret, or are "secret enough" to last until the code is
delivered and used. If this does not feel strong enough, users can turn on
additional verification that doesn't depend upon the secrecy of the channel.

The notion of a "magic wormhole" comes from the image of two distant wizards
speaking the same phrase at the same time, and causing a connection to be
established between them. Transferring files securely should be that easy.

## Design

The `wormhole` tool uses PAKE "Password-Authenticated Key Exchange", a family
of cryptographic algorithms that uses a short low-entropy password to
establish a strong high-entropy shared key. This key can then be used to
encrypt data. `wormhole` uses the SPAKE2 algorithm, due to Abdalla and
Pointcheval[1].

PAKE effectively trades off interaction against offline attacks. The only way
for a network attacker to learn the shared key is to perform a
man-in-the-middle attack during the initial connection attempt, and to
correctly guess the code being used by both sides. Their chance of doing this
is inversely proportional to the entropy of the wormhole code. The default is
to use a 16-bit code (use --code-length= to change this), so for each use of
the tool, an attacker gets a 1-in-65536 chance of success. As such, users can
expect to see many error messages before the attacker has a reasonable chance
of success.

## Timing

At present, the two clients must be run within about 3 minutes of each other,
as they will stop waiting after that time. This makes the tool most useful
for people who are having a real-time conversation already, and want to
graduate to a secure connection.

Future releases should increase that to several hours. This will enable a
mode in which two humans can decide on a code phrase offline, by choosing a
channel number and a few random words, and then go back home to their
computers later and begin the wormhole process. (This mode is already
supported, but is not currently easy to use because the two users must type
the phrases within three minutes of each other).

## Relays

The wormhole library requires a "Rendezvous Server": a simple relay that
delivers messages from one client to another. This allows the wormhole codes
to omit IP addresses and port numbers. The URL of a public server is baked
into the library for use as a default, and will be freely available until
volume or abuse makes it infeasible to support. Applications which desire
more reliability can easily run their own relay and configure their clients
to use it instead. Code for the Rendezvous Server is included in the library.

The file-transfer commands also use a "Transit Relay", which is another
simple server that glues together two inbound TCP connections and transfers
data on each to the other. The `wormhole send` file mode shares the IP
addresses of each client with the other (inside the encrypted message), and
both clients first attempt to connect directly. If this fails, they fall back
to using the transit relay. As before, the host/port of a public server is
baked into the library, and should be sufficient to handle moderate traffic.

The protocol includes provisions to deliver notices and error messages to
clients: if either relay must be shut down, these channels will be used to
provide information about alternatives.

## CLI tool

* `wormhole send --text TEXT`
* `wormhole send FILENAME`
* `wormhole send DIRNAME`
* `wormhole receive`

Both commands accept:

* `--relay-url URL` : override the rendezvous server URL
* `--transit-helper tcp:HOST:PORT`: override the Transit Relay
* `--code-length WORDS`: use more or fewer than 2 words for the code
* `--verify` : print (and ask user to compare) extra verification string

## Library

The `wormhole` module makes it possible for other applications to use these
code-protected channels. This includes blocking/synchronous support and
async/Twisted support, both for a symmetric scheme. The main module is named
`wormhole.blocking.transcribe`, to reflect that it is for
synchronous/blocking code, and uses a PAKE mode whereby one user transcribes
their code to the other. (internal names may change in the future).

The file-transfer tools use a second module named
`wormhole.blocking.transit`, which provides an encrypted record-pipe. It
knows how to use the Transit Relay as well as direct connections, and
attempts them all in parallel. `TransitSender` and `TransitReceiver` are
distinct, although once the connection is established, data can flow in
either direction. All data is encrypted (using nacl/libsodium "secretbox")
using a key derived from the PAKE phase. See
`src/wormhole/scripts/cmd_send.py` for examples.

## License, Compatibility

This library is released under the MIT license, see LICENSE for details.

This library is compatible with python2.7, 3.3, 3.4, and 3.5 . It is probably
compatible with py2.6, but the latest Twisted (15.5.0) is not. The
(daemonizing) 'wormhole server start' command does not yet work with py3, but
will in the future once Twisted itself is finished being ported.

This package depends upon the SPAKE2, pynacl, requests, and argparse
libraries. To run a relay server, use the async support, or run the unit
tests, you must also install Twisted.


#### footnotes

[1]: http://www.di.ens.fr/~pointche/Documents/Papers/2005_rsa.pdf "RSA 2005"
