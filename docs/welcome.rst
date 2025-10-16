
Welcome
=======

Get **things** from **one computer to another**, **safely**.


- **things**: are files, but can also be TCP or other network streams;
- **one computer to another**: is usually via a P2P connection, but can use a TURN-like relay depending on network conditions;
- **safely**: uses a PAKE construction to ensure high-security, end-to-end encryption with human-sized codes

We realize that some of that might sound like Magic, so let's examine each piece a little more closely.


Get Things
----------

`Traditionally <https://xkcd.com/949/>`_ transferring files is hard, even in 2025.
The first and most popular "Thing" is a file or directory.
For more on this, see the :doc:`file-transfer-protocol`

Magic Wormhole can send any sort of message, though, including messages making up streams -- for example, `Fowl <https://github.com/meejah/fowl>`_ does exactly this.
``fowl`` is built on a feature called "Dilation"; see :doc:`dilation-protocol` for more detail.


From One Computer to Another
----------------------------

Magic Wormhole has several methods to connect peers.
A peer uses network "hints" to suggest these ways to the other peer.
This allows considerable flexibility and can succeed in many different network conditions.

Initial and relatively small messages are sent via the "Mailbox" server.
This lets the two peers contact a well-known resource, and send the initial PAKE messages.
After establishing this shared key, the peers bootstrap via further messages over the Mailbox.
See :doc:`client-protocol` for more details.

For file-transfer, Mailbox messages contain the above-mentioned "hints" to establish a peer-to-peer connection.

If the peers are on the same LAN, they will communicate directly over the local network.
When one peer has a routable, public IP address they will also communicate directly.
In case both peers are behind a NAT (or otherwise can only make outbound connections) the "Transit Relay" server is used to relay messages (which are all encrypted) between the peers.

Regardless, in the end a connection is established to pass end-to-end encrypted messages between two peers (and only two peers).


Safely
------

Magic Wormhole exists (at least partly) so more software can use a cryptographic construct called "Password Authenticated Key Exchange" (PAKE), currently using the `SPAKE2 <https://datatracker.ietf.org/doc/rfc9382/>`_ variant.

This construct allows for human-sized codes to be used while still maintaining high security.
Each code is **one-time use only**, so attackers (e.g. a malicious Mailbox server) get only a single guess when attempting to subvert a connection.

If such a guess is successful, one of the two intended peers will notice: their connection will fail, typically with a "crowded" or "scary" error.

Early in the protocol a shared secret is established, after which all peer-to-peer traffic is encrypted with keys derived directly from the shared PAKE secret.
The section :doc:`attacks` has more details about failure modes.


Motivational Use-Cases
======================

.. raw:: html

    <script src="https://asciinema.org/a/6YLCEhZ2dGDhzr3u55OlhViZU.js" id="asciicast-6YLCEhZ2dGDhzr3u55OlhViZU" async="true"></script>

.. note::

    Unfortunately, asciinema doesn't work with screen-readers -- such users should see the Example below for a screen-reader friendly version


* ``wormhole send`` + ``wormhole receive`` (as demonstrated above) are provided by this package and allow transfer of arbitrary files and directories;
* `Warp <https://gitlab.gnome.org/World/warp>`_ is a Gnome GUI application to transfer files and directories (works with the CLI);
* `Wormhole <https://gitlab.com/lukas-heiligenbrunner/wormhole>`_: phone application for Android;
* `git withme <https://git.sr.ht/~meejah/git-withme>`_ allows two peers to directly use Git together (without any hosting service like GitLab or `gitolite <https://gitolite.com/gitolite/>`_);
* `Pear On <https://git.sr.ht/~meejah/pear-on>`_ allows two peers to use `tty-share; <https://github.com/elisescu/tty-share>`_ directly, without running or using a centralized server;
* See :doc:`ecosystem` for more implementations and applications


Detailed Overview of Magic Wormhole
===================================

This package provides a library and a command-line tool named
``wormhole``, which makes it possible to get arbitrary-sized files and
directories (or short pieces of text) from one computer to another. The
two endpoints are identified by using identical “wormhole codes”: in
general, the sending machine generates and displays the code, which must
then be typed into the receiving machine.

The codes are short and human-pronounceable, using a
phonetically-distinct wordlist. The receiving side offers tab-completion
on the codewords, so usually only a few characters must be typed.
Wormhole codes are single-use and do not need to be memorized.

-  PyCon 2016 presentation:
   `Slides <https://www.lothar.com/~warner/MagicWormhole-PyCon2016.pdf>`__,
   `Video <https://www.youtube.com/watch?v=oFrTqQw0_3c>`__

As of now (2023) the magic-wormhole protocol has several client
implementations; see :doc:`ecosystem`

Code: `github.com/magic-wormhole/magic-wormhole <https://github.com/magic-wormhole/magic-wormhole>`_
Documentation: `magic-wormhole.readthedocs.io <https://magic-wormhole.readthedocs.io/en/latest/>`_


Example
-------

Sender:

::

   % wormhole send README.md
   Sending 7924 byte file named 'README.md'
   On the other computer, please run: wormhole receive
   Wormhole code is: 7-crossover-clockwork

   Sending (<-10.0.1.43:58988)..
   100%|=========================| 7.92K/7.92K [00:00<00:00, 6.02MB/s]
   File sent.. waiting for confirmation
   Confirmation received. Transfer complete.

Receiver:

::

   % wormhole receive
   Enter receive wormhole code: 7-crossover-clockwork
   Receiving file (7924 bytes) into: README.md
   ok? (y/n): y
   Receiving (->tcp:10.0.1.43:58986)..
   100%|===========================| 7.92K/7.92K [00:00<00:00, 120KB/s]
   Received file written to README.md

Installation
------------

The easiest way to install magic-wormhole is to use a packaged version
from your operating system. If there is none, or you want to participate
in development, you can install from source.

MacOS / OS-X
~~~~~~~~~~~~

`Install Homebrew <https://brew.sh/>`__, then run
``brew install magic-wormhole``.

Linux (Debian/Ubuntu)
~~~~~~~~~~~~~~~~~~~~~

Magic-wormhole is available with ``apt`` in Debian 9 “stretch”, Ubuntu
17.04 “zesty”, and later versions:

::

   $ sudo apt install magic-wormhole

Linux (Fedora)
~~~~~~~~~~~~~~

Note: magic-wormhole `was removed from
Fedora <https://bugzilla.redhat.com/show_bug.cgi?id=2073777>`__ starting
in Fedora 37. So this command will only work on Fedora 36 and earlier.

::

   $ sudo dnf install magic-wormhole

Linux (openSUSE)
~~~~~~~~~~~~~~~~

::

   $ sudo zypper install python-magic-wormhole

Linux (Snap package)
~~~~~~~~~~~~~~~~~~~~

Many linux distributions (including Ubuntu) can install `“Snap”
packages <https://snapcraft.io/>`__. Magic-wormhole is available through
a third-party package (published by the “snapcrafters” group):

::

   $ sudo snap install wormhole

Windows
~~~~~~~

Chocolatey
^^^^^^^^^^

::

   $ choco install magic-wormhole

The binaries for Windows are provided from this project:
https://github.com/aquacash5/magic-wormhole-exe

Install from Source
~~~~~~~~~~~~~~~~~~~

Magic-wormhole is a Python package, and can be installed in the usual
ways. The basic idea is to do ``pip install magic-wormhole``, however to
avoid modifying the system’s python libraries, you probably want to put
it into a “user” environment (putting the ``wormhole`` executable in
``~/.local/bin/wormhole``) like this:

::

   pip install --user magic-wormhole

or put it into a virtualenv, like this:

::

   virtualenv venv
   source venv/bin/activate
   pip install magic-wormhole

You can then run ``venv/bin/wormhole`` without first activating the
virtualenv, so e.g. you could make a symlink from ``~/bin/wormhole`` to
``.../path/to/venv/bin/wormhole``, and then plain ``wormhole send`` will
find it on your ``$PATH``.

You probably *don’t* want to use ``sudo`` when you run ``pip``. This
tends to create
`conflicts <https://github.com/magic-wormhole/magic-wormhole/issues/336>`__ with
the system python libraries.

On OS X, you may need to pre-install ``pip``, and run
``$ xcode-select --install`` to get GCC, which is needed to compile the
``libsodium`` cryptography library during the installation process.

On Debian/Ubuntu systems, you may need to install some support libraries
first:

``$ sudo apt-get install python-pip build-essential python-dev libffi-dev libssl-dev``

On Linux, if you get errors like
``fatal error: sodium.h: No such file or directory``, either use
``SODIUM_INSTALL=bundled pip install magic-wormhole``, or try installing
the ``libsodium-dev`` / ``libsodium-devel`` package. These work around a
bug in pynacl which gets confused when the libsodium runtime is
installed (e.g. ``libsodium13``) but not the development package.

On Windows, python2 may work better than python3. On older systems,
``$ pip install --upgrade pip`` may be necessary to get a version that
can compile all the dependencies. Most of the dependencies are published
as binary wheels, but in case your system is unable to find these, it
will have to compile them, for which `Microsoft Visual C++
9.0 <https://support.microsoft.com/en-us/topic/the-latest-supported-visual-c-downloads-2647da03-1eea-4433-9aff-95f26a218cc0>`__
may be required.

Motivation
----------

-  Moving a file to a friend’s machine, when the humans can speak to
   each other (directly) but the computers cannot
-  Delivering a properly-random password to a new user via the phone
-  Supplying an SSH public key for future login use

Copying files onto a USB stick requires physical proximity, and is
uncomfortable for transferring long-term secrets because flash memory is
hard to erase. Copying files with ssh/scp is fine, but requires previous
arrangements and an account on the target machine, and how do you
bootstrap the account? Copying files through email first requires
transcribing an email address in the opposite direction, and is even
worse for secrets, because email is unencrypted. Copying files through
encrypted email requires bootstrapping a GPG key as well as an email
address. Copying files through Dropbox is not secure against the Dropbox
server and results in a large URL that must be transcribed. Using a URL
shortener adds an extra step, reveals the full URL to the shortening
service, and leaves a short URL that can be guessed by outsiders.

Many common use cases start with a human-mediated communication channel,
such as IRC, IM, email, a phone call, or a face-to-face conversation.
Some of these are basically secret, or are “secret enough” to last until
the code is delivered and used. If this does not feel strong enough,
users can turn on additional verification that doesn’t depend upon the
secrecy of the channel.

The notion of a “magic wormhole” comes from the image of two distant
wizards speaking the same enchanted phrase at the same time, and causing
a mystical connection to pop into existence between them. The wizards
then throw books into the wormhole and they fall out the other side.
Transferring files securely should be that easy.

Design
------

The ``wormhole`` tool uses PAKE “Password-Authenticated Key Exchange”, a
family of cryptographic algorithms that uses a short low-entropy
password to establish a strong high-entropy shared key. This key can
then be used to encrypt data. ``wormhole`` uses the SPAKE2 algorithm,
due to Abdalla and
Pointcheval\ `1 <https://www.di.ens.fr/~pointche/Documents/Papers/2005_rsa.pdf>`__.

PAKE effectively trades off interaction against offline attacks. The
only way for a network attacker to learn the shared key is to perform a
man-in-the-middle attack during the initial connection attempt, and to
correctly guess the code being used by both sides. Their chance of doing
this is inversely proportional to the entropy of the wormhole code. The
default is to use a 16-bit code (use –code-length= to change this), so
for each use of the tool, an attacker gets a 1-in-65536 chance of
success. As such, users can expect to see many error messages before the
attacker has a reasonable chance of success.

Timing
------

The program does not have any built-in timeouts, however it is expected
that both clients will be run within an hour or so of each other. This
makes the tool most useful for people who are having a real-time
conversation already, and want to graduate to a secure connection. Both
clients must be left running until the transfer has finished.

Relays
------

There are two servers involved, one of which you may never use.
- the "Mailbox Server";
- and a "Transit Relay"

The wormhole library requires a “Mailbox Server” (also known as the
“Rendezvous Server”): a simple WebSocket-based relay that delivers
messages from one client to another. This allows the wormhole codes to
omit IP addresses and port numbers. The URL of a public server is baked
into the library for use as a default, and will be freely available
until volume or abuse makes it infeasible to support. Applications which
desire more reliability can easily run their own relay and configure
their clients to use it instead. Code for the Mailbox Server is in a
separate package named ``magic-wormhole-mailbox-server`` and has
documentation
`here <https://github.com/magic-wormhole/magic-wormhole-mailbox-server/blob/master/docs/welcome.md>`__.
Both clients must use the same mailbox server. The default can be
overridden with the ``--relay-url`` option.

The file-transfer commands also use a “Transit Relay”, which is another
simple server that glues together two inbound TCP connections and
transfers data on each to the other (the moral equivalent of a TURN
server). The ``wormhole send`` file mode shares the IP addresses of each
client with the other (inside the encrypted message), and both clients
first attempt to connect directly. If this fails, they fall back to
using the transit relay. As before, the host/port of a public server is
baked into the library, and should be sufficient to handle moderate
traffic. Code for the Transit Relay is provided a separate package named
``magic-wormhole-transit-relay`` with instructions
`here <https://github.com/magic-wormhole/magic-wormhole-transit-relay/blob/master/docs/running.md>`__.
The clients exchange transit relay information during connection
negotiation, so they can be configured to use different ones without
problems. Use the ``--transit-helper`` option to override the default.

The protocol includes provisions to deliver notices and error messages
to clients: if either relay must be shut down, these channels will be
used to provide information about alternatives.

CLI tool
--------

-  ``wormhole send [args] --text TEXT``
-  ``wormhole send [args] FILENAME``
-  ``wormhole send [args] DIRNAME``
-  ``wormhole receive [args]``

Both commands accept additional arguments to influence their behavior:

-  ``--code-length WORDS``: use more or fewer than 2 words for the code
-  ``--verify`` : print (and ask user to compare) extra verification
   string

Tab-Completion
~~~~~~~~~~~~~~

Wormhole codes will tab-complete for receivers out-of-the-box.

If you desire shell tab-completion on sub-commands, we include generated
files `from
Click <https://click.palletsprojects.com/en/8.1.x/shell-completion/>`__
for Bash, Zsh and Fish shells in
`wormhole_completion.bash <https://github.com/magic-wormhole/magic-wormhole/blob/master/wormhole_complete.bash>`__
(or ``.zsh``, ``.fish``). Put this file in your favourite location and
add a line like ``source ~/wormhole_completion.bash`` to ``~/.bashrc``
(or similar for ``zsh`` and ``fish`` shells).

Library
-------

The ``wormhole`` module makes it possible for other applications to use
these code-protected channels. This includes Twisted support, and (in
the future) will include blocking/synchronous support too. See
:doc:`the API docs <api>` for details.

The file-transfer tools use a second module named ``wormhole.transit``,
which provides an encrypted record-pipe. It knows how to use the Transit
Relay as well as direct connections, and attempts them all in parallel.
``TransitSender`` and ``TransitReceiver`` are distinct, although once
the connection is established, data can flow in either direction. All
data is encrypted (using nacl/libsodium “secretbox”) using a key derived
from the PAKE phase. See ``src/wormhole/cli/cmd_send.py`` for examples.

Development
-----------

-  Bugs and patches at the `GitHub project
   page <https://github.com/magic-wormhole/magic-wormhole>`__.
-  Chat via `IRC <irc://irc.libera.chat/#magic-wormhole>`__:
   #magic-wormhole on irc.libera.chat
-  Chat via `Matrix <https://matrix.to/#/#magic-wormhole:matrix.org>`__:
   #magic-wormhole on matrix.org

To set up Magic Wormhole for development, you will first need to install
`virtualenv <https://docs.python.org/3/tutorial/venv.html>`__.

Once you’ve done that, ``git clone`` the repo, ``cd`` into the root of
the repository, and run:

::

   virtualenv venv
   source venv/bin/activate
   pip install --upgrade pip setuptools

Now your virtualenv has been activated. You’ll want to re-run
``source venv/bin/activate`` for every new terminal session you open.

To install Magic Wormhole and its development dependencies into your
virtualenv, run:

::

   pip install -e .[dev]

If you are using zsh, such as on macOS Catalina or later, you will have
to run ``pip install -e .'[dev]'`` instead.

While the virtualenv is active, running ``wormhole`` will get you the
development version.

Running Tests
~~~~~~~~~~~~~

Within your virtualenv, the command-line program ``trial`` will run the
test suite:

::

   trial wormhole

This tests the entire ``wormhole`` package. If you want to run only the
tests for a specific module, or even just a specific test, you can
specify it instead via Python’s standard dotted import notation, e.g.:

::

   trial wormhole.test.test_cli.PregeneratedCode.test_file_tor

Developers can also just clone the source tree and run ``tox`` to run
the unit tests on all supported (and installed) versions of
python: 3.10, 3.11, 3.12, 3.13.

Troubleshooting
~~~~~~~~~~~~~~~

Every so often, you might get a traceback with the following kind of
error:

::

   pkg_resources.DistributionNotFound: The 'magic-wormhole==0.9.1-268.g66e0d86.dirty' distribution was not found and is required by the application

If this happens, run ``pip install -e .[dev]`` again.

Other
~~~~~

Relevant `xkcd <https://xkcd.com/949/>`__ :-)

License, Compatibility
----------------------

This library is released under the MIT license, see LICENSE for details.

This library is compatible with Python 3.10, 3.11, 3.12, 3.13.

.. raw:: html

   <!-- footnotes -->
