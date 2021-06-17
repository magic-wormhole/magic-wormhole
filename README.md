# Magic Wormhole
[![PyPI](http://img.shields.io/pypi/v/magic-wormhole.svg)](https://pypi.python.org/pypi/magic-wormhole)
![Tests](https://github.com/magic-wormhole/magic-wormhole/workflows/Tests/badge.svg)
[![Windows Build Status](https://ci.appveyor.com/api/projects/status/w1bdniovwm4egfyg/branch/master?svg=true)](https://ci.appveyor.com/project/warner/magic-wormhole)
[![codecov.io](https://codecov.io/github/magic-wormhole/magic-wormhole/coverage.svg?branch=master)](https://codecov.io/github/magic-wormhole/magic-wormhole?branch=master)
[![Docs](https://readthedocs.org/projects/magic-wormhole/badge/?version=latest)](https://magic-wormhole.readthedocs.io)
[![Irc](https://img.shields.io/badge/irc.libera.chat-%23magic--wormhole-brightgreen)](https://web.libera.chat/)
[![Matrix](https://img.shields.io/badge/matrix.org-%23magic--wormhole-brightgreen)](https://matrix.to/#/#magic-wormhole:matrix.org)


Get things from one computer to another, safely.

This package provides a library and a command-line tool named `wormhole`,
which makes it possible to get arbitrary-sized files and directories
(or short pieces of text) from one computer to another. The two endpoints are
identified by using identical "wormhole codes": in general, the sending
machine generates and displays the code, which must then be typed into the
receiving machine.

The codes are short and human-pronounceable, using a phonetically-distinct
wordlist. The receiving side offers tab-completion on the codewords, so
usually only a few characters must be typed. Wormhole codes are single-use
and do not need to be memorized.

* PyCon 2016 presentation: [Slides](http://www.lothar.com/~warner/MagicWormhole-PyCon2016.pdf), [Video](https://youtu.be/oFrTqQw0_3c)

For complete documentation, please see https://magic-wormhole.readthedocs.io
or the docs/ subdirectory.

This program uses two servers, whose source code is kept in separate
repositories: the
[mailbox server](https://github.com/magic-wormhole/magic-wormhole-mailbox-server),
and the
[transit relay](https://github.com/magic-wormhole/magic-wormhole-transit-relay).

## License, Compatibility

Magic-Wormhole is released under the MIT license, see the `LICENSE` file for details.

This library is compatible with Python 3.6 and higher (tested against 3.6,
3.7, 3.8, and 3.9). It also still works with Python 2.7 and 3.5, although
these are no longer supported by upstream libraries like Cryptography, so it
may stop working at any time.

## Packaging, Installation

Magic Wormhole packages are included in many operating systems.

[![Packaging status](https://repology.org/badge/vertical-allrepos/magic-wormhole.svg)](https://repology.org/project/magic-wormhole/versions)

To install it without an OS package, follow the [Installation docs](https://magic-wormhole.readthedocs.io/en/latest/welcome.html#installation).
