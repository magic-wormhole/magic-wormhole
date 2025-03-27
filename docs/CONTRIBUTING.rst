Contributing to Magic Wormhole
==============================

Welcome!

There are many ways to contribute to Magic Wormhole, including:

- using the software;
- writing a bug report;
- improving the documentation;
- posting about Magic Wormhole on your favourite platform;
- fixing a bug;
- implementating a new feature;

Note that there are several separate repositories:

- `magic-wormhole <https://github.com/magic-wormhole/magic-wormhole>`_ (this one) the Python CLI client and library;
- `magic-wormhole-mailbox-server <https://github.com/magic-wormhole/magic-wormhole-mailbox-server>`_ the software running on the Mailbox server
- `magic-wormhole-transit-relay <https://github.com/magic-wormhole/magic-wormhole-transit-relay>`_ helper software for clients behind NATs
- `magic-wormhole-protocols <https://github.com/magic-wormhole/magic-wormhole-protocols>`_ high-level, programming-language agnostic specifications

There are also other client implementations in various states; see :doc:`ecosystem`.


Talking about Magic Wormhole
----------------------------

Many contributions benefit from starting with an "Issue" on GitHub to begin discussion.
This kind of discussion is appropriate for figuring out how to implement a feature (or even whether it makes sense), discussing how current features work (or don't!) and planning future work.

Sometimes, more ephemeral or real-time discussion is desired.
Realtime discussions are currently supported on:

- the `Libera <https://libera.chat/>`_ IRC network, channel `#magic-wormhole` (`web interface <https://web.libera.chat/#magic-wormhole>`_).
- on Matrix, at `#magic-wormhole:matrix.org <https://matrix.to/#/#magic-wormhole:matrix.org>`_.


Working on Magic Wormhole
-------------------------

For any non-trivial work in Magic Wormhole, it's usually a good idea to start with a ticket.
That said, some people learn by doing and may already have a branch or set of changes to propose: that's fine too!

The best way to propose a change is to "fork" the repository and start a new branch from "master" onto which you make commits.
Then, when you're ready, propose a Pull Request on the Magic Wormhole repository.

We typically try to provide feedback quickly, and you can poke `@meejah` specifically if nothing seems to be happening (e.g. tag them on a comment).

Visualize the State Machines
````````````````````````````

There are several co-operating state-machines in Magic Wormhole.

These are written using the `Automat <>`_ library, which can draw diagrams of the state-machines automatically.
To do this, run `automat-visualize wormhole` in the root directory of your checkout; images will appear in `.automat-visualize/*.png`.

Everything in the diagrams corresponds to real Python code that you can search for.
The ovals are states (decorated with `@m.state()` usually) and the boxes on edges represent an input (`@m.input()`) with corresponding outputs (`@m.output()` -decorated methods).

Only "output" methods can do "real work" (i.e. have a function body).


Testing
```````

There are unit-tests covering much of the code-base.
New code should be covered by unit-tests.

To run the tests, see the `Makefile` / `make test` target.
There are also `Tox <>`_ environments which is what Continuous Integration (GitHub Actions) uses to run the tests.

For example: `tox -e py311` will run the tests under Python 3.11.

It's also possible to set up a completely local manual test by running the Mailbox server and using `wormhole --relay-url ws://localhost:4000/v1/` to reach that local server instead of the default, public one.


Coding Conventions
``````````````````

While our CI does run some linting checks, there can be a confusing mix of code conventions sometimes.
New code should follow the following patterns:

- use `@inlineCallbacks` and `yield`. We would like to transition to `async-def`, `await` and `ensureDeferred` but are not there yet;
- use `pytest`-style tests; use `@pytest.fixture()` to set up pre-requisites for test; use plain `assert` statements;



Other Stuff That's Confusing?
`````````````````````````````

- `eventually()` and `EventualQueue`
- global reactor?
- 
