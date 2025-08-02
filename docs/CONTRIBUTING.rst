Contributing to Magic Wormhole
==============================

Welcome!

There are many ways to contribute to Magic Wormhole, including:

- using the software;
- writing a bug report;
- improving the documentation;
- posting about Magic Wormhole on your favourite platform;
- fixing a bug;
- implementing a new feature;

Note that there are several separate repositories:

- `magic-wormhole <https://github.com/magic-wormhole/magic-wormhole>`_ (this one) the Python CLI client and library;
- `magic-wormhole-mailbox-server <https://github.com/magic-wormhole/magic-wormhole-mailbox-server>`_ the software running on the Mailbox server
- `magic-wormhole-transit-relay <https://github.com/magic-wormhole/magic-wormhole-transit-relay>`_ helper software for clients behind NATs
- `magic-wormhole-protocols <https://github.com/magic-wormhole/magic-wormhole-protocols>`_ high-level, programming-language agnostic specifications

There are also other client implementations in various states; see :doc:`ecosystem`.


Talking About Magic Wormhole
----------------------------

Many contributions benefit from starting with an "Issue" on GitHub to begin discussion.
This kind of discussion is appropriate for figuring out how to implement a feature (or even whether it makes sense), discussing how current features work (or don't!) and planning future work.

Real-time and ephemeral discussions are currently supported on:

- the `Libera <https://libera.chat/>`_ IRC network, channel ``#magic-wormhole`` (`web interface <https://web.libera.chat/#magic-wormhole>`_).
- on Matrix, at `#magic-wormhole:matrix.org <https://matrix.to/#/#magic-wormhole:matrix.org>`_.


Working on Magic Wormhole
-------------------------

For any non-trivial work in Magic Wormhole, it's usually a good idea to start with a ticket.
That said, some people learn by doing and may already have a branch or set of changes to propose: that's fine too!

The best way to propose a change is to "fork" the repository and start a new branch from "master" onto which you make commits.
Then, when you're ready, propose a Pull Request on the Magic Wormhole repository.

We typically try to provide feedback quickly, and you can poke ``@meejah`` specifically if nothing seems to be happening (e.g. tag that handle on a comment).


Visualize the State Machines
````````````````````````````

There are several co-operating state-machines in Magic Wormhole.

These are written using the `Automat <https://automat.readthedocs.io/en/latest/>`_ library, which can draw diagrams of the state-machines automatically.
To do this, run ``automat-visualize wormhole`` in the root directory of your checkout; images will appear in ``.automat-visualize/*.png``.

Everything in the diagrams corresponds to real Python code that you can search for.
The ovals are states (decorated with ``@m.state()`` usually) and the boxes on edges represent an input (``@m.input()``) with corresponding outputs (``@m.output()`` -decorated methods).

Only "output" methods can do "real work" (i.e. have a function body).


Testing
```````

There are unit-tests covering much of the code-base.
New code should be covered by unit-tests.

To run the tests, see the ``Makefile`` / ``make test`` target.
There are also `Tox <https://tox.wiki>`_ environments which is what Continuous Integration (GitHub Actions) uses to run the tests.

For example: ``tox -e py311`` will run the tests under Python 3.11.

It's also possible to set up a completely local manual test by running the Mailbox server and using ``wormhole --relay-url ws://localhost:4000/v1/`` to reach that local server instead of the default public one.

**NOTE:** do not import or use ``unittest`` or ``twisted.trial.unittest``, as any reactor-using tests (and there are lots of those) will become confused as to which reactor to use and things will hang.


Coding Conventions
``````````````````

While our CI does run some linting checks, there can be a confusing mix of code conventions sometimes.
New code should follow the following patterns:

- use ``@inlineCallbacks`` and ``yield``. We would like to transition to ``async-def``, ``await`` and ``ensureDeferred`` but are not there yet (exception: the tests);
- use ``pytest``-style tests;
- use ``@pytest.fixture()`` to set up pre-requisites for tests;
- use plain ``assert`` statements;
- use ``async def`` for test functions with ``@pytest_twisted.ensureDeferred`` decorator;
- long lines should split up "one argument per line" style;
- new functions should have docstrings;
- new functionality should have prose documentation;
- features and changes should be mentioned in ``NEWS.md``;
- checking linting and styling with ``ruff`` and/or ``pyflakes`` (e.g. ``tox -e flake8less``)


Other Stuff That's Confusing
----------------------------


``eventually()`` and ``EventualQueue``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This pattern ensures that an async thing happens only after at least one "reactor turn".

One drawback -- or at least thing to be aware of -- is that functions doing this (even "functions they call" etc etc) will have to use a functioning reactor of some sort to work.
The tests are already set up to do this.

Reasons for this include: not having to think as much about stack-depths; work around Automat limitation (of not being able to call ``@input`` functions from ``@output`` functions in a reliable way).


global reactor
~~~~~~~~~~~~~~

It is ideal if things that use a reactor get it passed to them (instead of relying on importing "the global reactor" in Twisted).
Although many things do already accept a reactor parameter, not everything does.

New code should be written to accept a reactor argument.


Other Confusing Things
~~~~~~~~~~~~~~~~~~~~~~~

Are you confused about a thing? Please: **reach out and ask us!**
