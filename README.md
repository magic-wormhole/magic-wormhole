# Magic Wormhole
[![Build Status](https://travis-ci.org/warner/magic-wormhole.svg?branch=master)](https://travis-ci.org/warner/magic-wormhole)
[![Windows Build Status](https://ci.appveyor.com/api/projects/status/mfnn5rsyfnrq576a/branch/master?svg=true)](https://ci.appveyor.com/project/warner/magic-wormhole)
[![codecov.io](https://codecov.io/github/warner/magic-wormhole/coverage.svg?branch=master)](https://codecov.io/github/warner/magic-wormhole?branch=master)

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

This library is released under the MIT license, see LICENSE for details.

This library is compatible with python2.7, 3.4, 3.5, and 3.6 . It is
probably compatible with py2.6, but the latest Twisted (>=15.5.0) is
not.

