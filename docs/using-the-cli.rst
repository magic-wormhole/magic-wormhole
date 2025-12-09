
.. _cli_details:

Using the CLI
=============

The ``wormhole`` CLI uses sub-commands to split up the main areas of functionality.
Passing the ``--help`` option provides many details.

This document exists as a supplement to the CLI documentation.
We assume you've already gotten the software via the :ref:`installation` instructions.

In general, the ``wormhole`` command and its sub-commands all expect a human user and do not attempt to keep the output machine-readable or even necessarily predictable between releases.

If you are new to Magic Wormhole, start with the :ref:`cli_overview` examples.


Environment Variables
---------------------

Most options have what we consider good defaults, and several can be controlled with environment variables.

- ``WORMHOLE_RELAY_URL``: same as ``--relay-url`` to configure the Mailbox
- ``WORMHOLE_TRANSIT_HELPER``: same as ``--transit-helper`` to configure the Transit relay
- ``WORMHOLE_ACCEPT_FILE``: same as ``--accept-file`` to avoid "y / n " prompts
- ``WORMHOLE_QR``: same as ``--qr`` or ``--no-qr``, e.g. ``export WORMHOLE_QR=0`` turns off the QR code.


Aliases
-------

There are a few aliases not mentioned via ``--help``:

- ``wormhole tx`` -> ``wormhole send``
- ``wormhole rx`` -> ``wormhole receive``
  - also ``recv`` and ``recieve``


Deeper Meaning of Options
-------------------------

Understanding of most options should come from their existing "short help" found by using the ``--help`` flag.

Some more explanation of selected global options:

- ``--appid`` mostly useful for developers, every distinct use-case of Magic Wormhole should have a distinct "AppID" (e.g. the "file transfer" that most people are familiar with uses ``lothar.com/wormhole/text-or-file-xfer``). Each distinct AppID represents a completely separate namespace. That is, any AppID could be hosted on a completely different Mailbox server.
- ``--relay-url`` may be used to reach a different Mailbox server (all clients need to use the same server to successfully connect)
- ``--transit-helper`` the third-party relay to use in case a direct connection is not possible. You could run your own and use it for all your own transfers (only) without e.g. using an alternative Mailbox relay


Network Location Privacy
~~~~~~~~~~~~~~~~~~~~~~~~

For users interested in *Tor for location privacy*, there are several options in both ``wormhole send`` and ``wormhole receive``:

- ``--tor`` to turn on Tor usage (which will try some common defaults to connect to a local Tor daemon)
- ``--launch-tor`` to use a freshly-launched private Tor instance (you must install Tor yourself first)
- ``--tor-control-port`` specify an endpoint descriptor to connect to an already-running Tor control port (e.g. ``--tor-control-port unix:/var/run/tor/control`` or ``--tor-control-port tcp:localhost:9051``).


Developer Assistance
~~~~~~~~~~~~~~~~~~~~

For developers debugging or testing things, these can be useful options:

- ``-0`` **do not use this** for anything other than throwaway data and testing; uses mailbox "0" and no password
- ``--code-length`` change the number of random words
- ``--no-listen`` do not set up a TCP local listener at all
- ``--debug-state`` dump information about the state-transitions of any or all state-machines


Receiver Goes First
~~~~~~~~~~~~~~~~~~~

Sometimes it can be useful to go "backwards"; usually, the "wormhole send" side allocates a nameplate + code, and that side's human communicates the code to the peer.
However, it's possible for the _receiving_ side to do the code allocation.
This would be in a situation where you're asking for some files, and want to say something like, "please send the files to code 1-foo-bar".

For this mode, the receiver can run ``wormhole receive --allocate`` which will allocate and print out a code.
Then, the sending side can run ``wormhole send --code <the code> README.rst`` or similar to send files to the waiting receiver.

We find it easier to explain Magic Wormhole without getting into a discussions of "who should go first" and thus the default is to have the sender go first.
However, the protocol doesn't really care who "allocated" versus who "typed in" the code at the network or cryptography level.
