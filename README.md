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

* PyCon 2016 presentation: [Slides](http://www.lothar.com/~warner/MagicWormhole-PyCon2016.pdf), [Video](https://youtu.be/oFrTqQw0_3c)

## Example

Sender:

```
% wormhole send README.md
Sending 7924 byte file named 'README.md'
On the other computer, please run: wormhole receive
Wormhole code is: 7-crossover-clockwork
 
Sending (<-10.0.1.43:58988)..
100%|=========================| 7.92K/7.92K [00:00<00:00, 6.02MB/s]
File sent.. waiting for confirmation
Confirmation received. Transfer complete.
```

Receiver:

```
% wormhole receive
Enter receive wormhole code: 7-crossover-clockwork
Receiving file (7924 bytes) into: README.md
ok? (y/n): y
Receiving (->tcp:10.0.1.43:58986)..
100%|===========================| 7.92K/7.92K [00:00<00:00, 120KB/s]
Received file written to README.md
```


## Installation

```$ pip install magic-wormhole```

On Debian/Ubuntu systems, you may first need `apt-get install python-pip
build-essential python-dev libffi-dev`. On Fedora it's `python-devel`
and `libffi-devel`. On OS-X, you may need to install `pip` and run
`xcode-select --install` to get GCC. Note: on Windows, only python2 is
currently supported.

If you get errors like `fatal error: sodium.h: No such file or directory` on
Linux, either use `SODIUM_INSTALL=bundled pip install magic-wormhole`, or try
installing the `libsodium-dev` / `libsodium-devel` package. These work around
a bug in pynacl which gets confused when the libsodium runtime is installed
(e.g. `libsodium13`) but not the development package.

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
speaking the same enchanted phrase at the same time, and causing a mystical
connection to pop into existence between them. The wizards then throw books
into the wormhole and they fall out the other side. Transferring files
securely should be that easy.

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

The program does not have any built-in timeouts, however it is expected that
both clients will be run within an hour or so of each other. This makes the
tool most useful for people who are having a real-time conversation already,
and want to graduate to a secure connection. Both clients must be left
running until the transfer has finished.

## Relays

The wormhole library requires a "Rendezvous Server": a simple WebSocket-based
relay that delivers messages from one client to another. This allows the
wormhole codes to omit IP addresses and port numbers. The URL of a public
server is baked into the library for use as a default, and will be freely
available until volume or abuse makes it infeasible to support. Applications
which desire more reliability can easily run their own relay and configure
their clients to use it instead. Code for the Rendezvous Server is included
in the library.

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

* `wormhole send [args] --text TEXT`
* `wormhole send [args] FILENAME`
* `wormhole send [args] DIRNAME`
* `wormhole receive [args]`

Both commands accept additional arguments to influence their behavior:

* `--code-length WORDS`: use more or fewer than 2 words for the code
* `--verify` : print (and ask user to compare) extra verification string

## Library

The `wormhole` module makes it possible for other applications to use these
code-protected channels. This includes Twisted support, and (in the future)
will include blocking/synchronous support too. See docs/api.md for details.

The file-transfer tools use a second module named `wormhole.transit`, which
provides an encrypted record-pipe. It knows how to use the Transit Relay as
well as direct connections, and attempts them all in parallel.
`TransitSender` and `TransitReceiver` are distinct, although once the
connection is established, data can flow in either direction. All data is
encrypted (using nacl/libsodium "secretbox") using a key derived from the
PAKE phase. See `src/wormhole/cli/cmd_send.py` for examples.

## License, Compatibility

This library is released under the MIT license, see LICENSE for details.

This library is compatible with python2.7, 3.3, 3.4, and 3.5 . It is probably
compatible with py2.6, but the latest Twisted (>=15.5.0) is not.


#### footnotes

[1]: http://www.di.ens.fr/~pointche/Documents/Papers/2005_rsa.pdf "RSA 2005"
