# Magic Wormhole
[![PyPI](http://img.shields.io/pypi/v/magic-wormhole.svg)](https://pypi.python.org/pypi/magic-wormhole)
[![Build Status](https://travis-ci.org/warner/magic-wormhole.svg?branch=master)](https://travis-ci.org/warner/magic-wormhole)
[![Windows Build Status](https://ci.appveyor.com/api/projects/status/mfnn5rsyfnrq576a/branch/master?svg=true)](https://ci.appveyor.com/project/warner/magic-wormhole)
[![codecov.io](https://codecov.io/github/warner/magic-wormhole/coverage.svg?branch=master)](https://codecov.io/github/warner/magic-wormhole?branch=master)
[![Docs](https://readthedocs.org/projects/magic-wormhole/badge/?version=latest)](https://magic-wormhole.readthedocs.io)

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

## License, Compatibility

Magic-Wormhole is released under the MIT license, see the `LICENSE` file for details.

This library is compatible with Python 3.5 and higher (tested against 3.5,
3.6, 3.7, and 3.8). It also still works with Python 2.7.

## Packaging, Installation

Magic Wormhole packages are included in many operating systems.

[![Packaging status](https://repology.org/badge/vertical-allrepos/magic-wormhole.svg)](https://repology.org/project/magic-wormhole/versions)

To install it without an OS package, follow the [Installation docs](https://magic-wormhole.readthedocs.io/en/latest/welcome.html#installation).
