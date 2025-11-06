The Magic-Wormhole Ecosystem
============================

This page attempts to summarize the current state of various codebases that implement or extend the Magic Wormhole protocols.
If you feel something is missing, please open an Issue or (better yet) a Pull Request at https://github.com/magic-wormhole/magic-wormhole

This document represents our best knowledge as of April, 2025.


Documentation
-------------

There are many documents in this repository itself, which tend to be more Python-specific.
The `Magic Wormhole Protocols <https://github.com/magic-wormhole/magic-wormhole-protocols>`_ repository aims to collect programming-language-agnostic documentation and specifications of the core Magic Wormhole protocol and extensions.
Currently, it is still not complete, and sometimes fails to describe existing features or describes enhancements that don't yet have an implementation.

Rendered versions of this document exist at `magic-wormhole.readthedocs.io <https://magic-wormhole.readthedocs.io/en/latest/>`_.


Protocols Overview
------------------

There are several main pieces of the protocol:

* the core "mailbox protocol", spoken via the `mailbox server <https://github.com/magic-wormhole/magic-wormhole-mailbox-server>`_ (formerly called "rendezvous server" in some places) and client implementations
* the "transit relay" protocol, spoken by the `transit relay server <https://github.com/magic-wormhole/magic-wormhole-transit-relay>`_
* the Dilation protocol (which depends on the core mailbox protocol), spoken by client implementations

It is possible for clients to specify zero or many Transit Relays, but both peers must use the same Mailbox Server to successfully communicate (even if they end up with a direct, non-relay connection).

This document does not try to describe any of these protocols in detail.


Implementations and Support
---------------------------

There are several main features of the core protocol; while implementations strive for completeness, we document here what features those implementations actually support.

The only known Mailbox Server is https://github.com/magic-wormhole/magic-wormhole-mailbox-server
The only known Transit Relay implementation is https://github.com/magic-wormhole/magic-wormhole-transit-relay

The Dilation protocol itself is still in development (with a basically-complete Python implementation, a servicable specification and an in-development Rust implementation).

The "Seeds" extension is a plan with no known implementations or specifications

The "Permissions" extension has a "proof-of-concept" Python client and server implementation and a specification but is not in any releases.


Features Supported by Implementations
-------------------------------------

The separate features represented in the below table are:

* *Core*: the basic client-visible features of the core mailbox protocol to `open`, `allocate`, `claim` and `close` a mailbox (as well as `add` messages to it)
* *Reconnect*: client state and behavior to re-connect to an in-progress or existing mailbox (e.g. in case of network failure, etc)
* *File Transfer v1* ("File v1"): the existing file-transfer protocol (allowing single Files or Directories or text-messages to be transferred in one direction)
* *Permissions*: an extension to allow the server to request additional work or information from clients before allowing access.
* *Dilation*: the core Dilation protocol
* *Dilated File Transfer* ("Dilated Transfer"): a more fully-featured file-transfer protocol on top of *Dilation*.


Support is described as:

* *Full*: supports the feature
* *Partial*: supports the feature, with some caveats
* *Experimental*: supports the feature fully, but not quite final (e.g. needs flags to turn on, API may change, etc)
* *PoC*: some level of implementation exists, but not enough to be considered "Experimental".
* *No*: the feature is not supported


.. list-table:: Implementation Support
    :widths: 25 15 15 15 15 15
    :header-rows: 1

    * - Language
      - Core
      - Reconnect
      - File v1
      - Dilation
      - Dilated Transfer

    * - `Python <https://github.com/magic-wormhole/magic-wormhole>`_
      - Full
      - Full
      - Full
      - Experimental
      - PoC

    * - `Rust <https://github.com/magic-wormhole/magic-wormhole.rs/>`_
      - Full
      - Partial
      - Partial
      - PoC
      - No

    * - `Haskell <https://github.com/LeastAuthority/haskell-magic-wormhole/>`_
      - Full
      - No
      - Full
      - No
      - No

    * - `Go <https://github.com/psanford/wormhole-william>`_
      - Full
      - ???
      - Full
      - No
      - No

Notes:
* the Rust implementation v1 file-transfer doesn't support text-messages, or directory transfer (although it will produce a tarball and send it, that is not automatically unpacked on the other side)
* there are two parts to the Haskell implementation: a library, and `a Haskell file-transfer CLI client <https://github.com/LeastAuthority/wormhole-client>`_


End User / Client Applications
------------------------------

Based on the above libraries, there are several end-user applications targeting different platforms.
Unless otherwise noted, these "inherit" any limitations of their langauge's library implementation from the above table.

Library and CLI
~~~~~~~~~~~~~~~

* `magic-wormhole <https://github.com/magic-wormhole/magic-wormhole>`_ the Python reference implementation and CLI (the command-line program is called ``wormhole`` in most distributions)
* `wormhole-william <https://github.com/psanford/wormhole-william>`_ is a Go library and CLI for file-transfer
* `magic-wormhole.rs <https://github.com/magic-wormhole/magic-wormhole.rs/>`_ provides a library and CLI for file-transfer
* `haskell-magic-wormhole <https://github.com/LeastAuthority/haskell-magic-wormhole>`_ and `wormhole-client <https://github.com/LeastAuthority/wormhole-client>`_ are a library and CLI for file-transfer in Haskell
* `dart bindings <https://github.com/LeastAuthority/dart_wormhole_william>`_ allowing Wormhole William to be used in Flutter.


GUIs for Desktop, Mobile, Web
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* `Warp <https://apps.gnome.org/Warp/>`_ is a GNOME GUI written in Rust
* `Winden <https://winden.app/>`_ is a Web client and deployment (using the Go implementation via WASM)
* `Destiny <https://f-droid.org/packages/com.leastauthority.destiny/>`_ is an Android (and iOS) app using Flutter (with the Go implementation for wormhole). Also on proprietary app stores.
* `Wormhole <https://gitlab.com/lukas-heiligenbrunner/wormhole>`_ for Android. Based on the Rust implementation.
* `Mobile Wormhole <https://github.com/pavelsof/mobile-wormhole>`_ for Android (also `on f-droid <https://github.com/pavelsof/mobile-wormhole>`_. Based on the Python implementation, using Kivy
* `Wormhole William Mobile <https://github.com/psanford/wormhole-william-mobile>`_ for Android and iOS.
* `Rymdport <https://github.com/Jacalz/rymdport>`_ is a cross-platform graphical desktop application based on wormhole-william.


Non-File-Transfer Uses
~~~~~~~~~~~~~~~~~~~~~~

We can use Wormhole (especially with Dilation) for all kinds of peer protocols.

* `git-withme <https://sr.ht/~meejah/git-withme>`_: use Git directly between two peers, no GitLab or similar host required;
* `Pear On <https://sr.ht/~meejah/pear-on/>`_: share a terminal with one or more peers (``tty-share`` without a central server);
* Port-forwarding: over the classic Transit protocol in the `rust implementation <https://github.com/magic-wormhole/magic-wormhole.rs/blob/e6ddc75c63ba030d5681cac04ca3e5a2262acc50/src/forwarding.rs#L1>`_ and over the Dilation protocol in Python as `fowl <https://github.com/meejah/fowl>`_ (foward-over-wormhole, locally).


Integrations
------------

These use the basic file-transfer functionality of the protocol, but build it in to some other application.

* `tmux-wormhole <https://github.com/gcla/tmux-wormhole>`_ a tmux plugin allowing use of file-transfer from within a tmux session (based on the Go implementation).
* `termshark <https://github.com/gcla/termshark/>`_ integrates ``wormhole-william`` (the Go implementation) to facilitate transfer of ``.pcap`` files (see the `termshark User Guide <https://github.com/gcla/termshark/blob/master/docs/UserGuide.md#transfer-a-pcap-file>`_


Mailbox-Only Uses
~~~~~~~~~~~~~~~~~

It's possible to do interesting things without ever gaining a direct peer connection.
Here are some we know of:

* Invite / key-exchange: `Magic Folder <https://magic-folder.readthedocs.io/en/latest/invites.html>`_ implements a custom protocol to do "introduction" / key-exchange.

* Invite / configuration exchange: `Tahoe-LAFS <https://tahoe-lafs.readthedocs.io/en/latest/magic-wormhole-invites.html>`_ uses Magic Wormhole to exchange configuration (and keys) for participants to join a Grid.
