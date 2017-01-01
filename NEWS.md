
User-visible changes in "magic-wormhole":

## Release 0.9.1 (01-Jan-2017)

The `wormhole` client's `--transit-helper=` argument can now include a
"relay priority" via a numerical `priority=` field, e.g.
`--transit-helper tcp:example.org:12345:priority=2.5`. Clients exchange
transit relay suggestions, then try to use the highest-priority relay
first, falling back to others after a few seconds if necessary. Direct
connections are always preferred to a relay. Clients running 0.9.0 or
earlier will ignore priorities, and unmarked relay arguments have an
implicit priority of 0. (#103)

Other changes:

* clients now tolerate duplicate peer messages: in the future, this will
  help clients recover from intermittent rendezvous connections (#121)
* rendezvous server: ensure release() and close() are idempotent (from
  different connections), also for lost-connection recovery (#118)
* transit server: respect --blur-usage= by not logging connections
* README: note py3.6 compatibility

Thanks to xloem, kneufeld, and meejah for their help this cycle.


## Release 0.9.0 (24-Dec-2016)

This release fixes an important "Transit Relay" bug that would have
prevented future versions from using non-default relay servers. It is
now easier to run `wormhole` as a subprocess beneath some other program
(the long term goal is to provide a nice API, but even with one, there
will be programs written in languages without Wormhole bindings that may
find it most convenient to use a subprocess).

* fix `--transit-helper=`: Older versions had a bug that broke
  file/directory transfers when the two sides offered different
  transit-relay servers. This was fixed by deduplicating relay hints and
  adding a new kind of relay handshake. Clients running 0.9.0 or higher
  now require a transit-relay server running 0.9.0 or higher. (#115)
* `wormhole receive`: reject transfers when the target does not appear
  to have enough space (not available on windows) (#91)
* CLI: emit pacifier message when key-verification is slow (#29)
* add `--appid=` so wrapping scripts can use a distinct value (#113)
* `wormhole send`: flush output after displaying code, for use in
  scripts (#108)
* CLI: print progress messages to stderr, not stdout (#99)
* add basic man(1) pages (#69)

Many thanks to patch submitters for this release: Joey Hess, Jared
Anderson, Antoine Beaupré, and to everyone testing and filing issues on
Github.


## Release 0.8.2 (08-Dec-2016)

* CLI: add new "wormhole ssh invite" and "wormhole ssh accept" commands, to
  facilitate appending your `~/.ssh/id_*.pub` key into a
  suitably-permissioned remote `~/.ssh/authorized_keys` file. These commands
  are experimental: the syntax might be changed in the future, or they might
  be removed altogether.
* CLI: "wormhole recv" and "wormhole recieve" are now accepted as aliases for
  "wormhole receive", to help bad spelers :)
* CLI: improve display of abbreviated file sizes
* CLI: don't print traceback upon "normal" errors
* CLI: when target file already exists, don't reveal that fact to the sender,
  just say "transfer rejected"
* magic-wormhole now depends upon `Twisted[tls]`, which will cause pyOpenSSL
  and the `cryptography` package to be installed. This should prevent a
  warning about the "service_identity" module not being available.
* other smaller internal changes

Thanks to everyone who submitted patches in this release cycle: anarcat,
Ofekmeister, Tom Lowenthal, meejah, dreid, and dkg. And thanks to the many
bug reporters on Github!


## Release 0.8.1 (27-Jul-2016)

This release contains mostly minor changes.

The most noticeable is that long-lived wormholes should be more reliable now.
Previously, if you run `wormhole send` but your peer doesn't run their
`receive` for several hours, a NAT/firewall box on either side could stop
forwarding traffic for the idle connection (without sending a FIN or RST to
properly close the socket), causing both sides to hang forever and never
actually connect. Now both sides send periodic keep-alive messages to prevent
this.

In addition, by switching to "Click" for argument parsing, we now have short
command aliases: `wormhole tx` does the same thing as `wormhole send`, and
`wormhole rx` is an easier-to-spell equivalent of `wormhole receive`.

Other changes:

* CLI: move most arguments to be attached to the subcommand (new: `wormhole
  send --verify`) rather than on the "wormhole" command (old: `wormhole
  --verify send`). Four arguments remain on the "wormhole" command:
  `--relay-url=`, `--transit-helper=`, `--dump-timing=`, and `--version`.
* docs: add links to PyCon2016 presentation
* reject wormhole-codes with spaces with a better error message
* magic-wormhole ought to work on windows now
* code-input tab-completion should work on stock OS-X python (with libedit)
* sending a directory should restore file permissions correctly
* server changes:
  * expire channels after two hours, not 3 days
  * prune channels more accurately
  * improve munin plugins for server monitoring

Many thanks to the folks who contributed to this release, during the PyCon
sprints and afterwards: higs4281, laharah, Chris Wolfe, meejah, wsanchez,
Kurt Neufeld, and Francois Marier.


## Release 0.8.0 (28-May-2016)

This release is completely incompatible with the previous 0.7.6 release.
Clients using 0.7.6 or earlier will not even notice clients using 0.8.0
or later.

* Overhaul client-server websocket protocol, client-client PAKE
  messages, per-message encryption-key derivation, relay-server database
  schema, SPAKE2 key-derivation, and public relay URLs. Add version
  fields and unknown-message tolerance to most protocol steps.
* Hopefully this will provide forward-compatibility with future protocol
  changes. I have several on my list, and the version fields should make
  it possible to add these without a flag day (at worst a "flag month").
* User-visible changes are minimal, although some operations should be
  faster because we no longer need to wait for ACKs before proceeding.
* API changes: `.send_data()/.get_data()` became `.send()/.get()`,
  neither takes a phase= argument (the Wormhole is now a record pipe)
  `.get_verifier()` became `.verify()` (and waits to receive the
  key-confirmation message before firing its Deferred), wormholes are
  constructed with a function call instead of a class constructor,
  `close()` always waits for server ack of outbound messages. Note that
  the API remains unstable until 1.0.0 .
* misc/munin/ contains plugins for relay server operators


## Release 0.7.6 (08-May-2016)

* Switch to "tqdm" for nicer CLI progress bars.
* Fail better when input-code is interrupted (prompt user to hit Return,
  rather than hanging forever)
* Close channel upon error more reliably.
* Explain WrongPasswordError better.
* (internal): improve --dump-timing instrumentation and rendering.

Compatibility: this remains compatible with 0.7.x, and 0.8.x is still
expected to break compatibility.


## Release 0.7.5 (20-Apr-2016)

* The CLI tools now use the Twisted-based library exclusively.
* The blocking-flavor "Transit" library has been removed. Transit is the
  bulk-transfer protocol used by send-file/send-directory. Upcoming protocol
  improvements (performance and connectivity) proved too difficult to
  implement in a blocking fashion, so for now if you want Transit, use
  Twisted.
* The Twisted-flavor "Wormhole" library now uses WebSockets to connect,
  rather than HTTP. The blocking-flavor library continues to use HTTP.
  "Wormhole" is the one-message-at-a-time relay-based protocol, and is
  used to set up Transit for the send-file and send-directory modes of
  the CLI tool.
* Twisted-flavor input_code() now does readline-based code entry, with
  tab completion.
* The package now installs two executables: "wormhole" (for send and
  receive), and "wormhole-server" (to start and manage the relay
  servers). These may be re-merged in a future release.

Compatibility:

* This release remains compatible with the previous ones. The next major
  release (0.8.x) will probably break compatibility.

Packaging:

* magic-wormhole now depends upon "Twisted" and "autobahn" (for WebSockets).
  Autobahn pulls in txaio, but we don't support it yet (a future version of
  magic-wormhole might).
* To work around a bug in autobahn, we also (temporarily) depend upon
  "pytrie". This dependency will be removed when the next autobahn release is
  available.


## Release 0.7.0 (28-Mar-2016)

* `wormhole send DIRNAME/` used to deal very badly with the trailing slash
  (sending a directory with an empty name). This is now fixed.
* Preliminary Tor support was added. Install `magic-wormhole[tor]`, make sure
  you have a Tor executable on your $PATH, and run `wormhole --tor send`.
  This will launch a new Tor process. Do not use this in anger/fear until it
  has been tested more carefully. This feature is likely to be unstable for a
  while, and lacks tests.
* The relay now prunes unused channels properly.
* Added --dump-timing= to record timeline of events, for debugging and
  performance improvements. You can combine timing data from both sides to
  see where the delays are happening. The server now returns timestamps in
  its responses, to measure round-trip delays. A web-based visualization tool
  was added in `misc/dump-timing.py`.
* twisted.transit was not properly handling multiple records received in a
  single chunk. Some producer/consumer helper methods were added. You can now
  run e.g. `wormhole --twisted send` to force the use of the Twisted
  implementation.
* The Twisted wormhole now uses a persistent connection for all relay
  messages, which should be slightly faster.
* Add `--no-listen` to prevent Transit from listening for inbound connections
  (or advertising any addresses): this is only useful for testing.
* The tests now collect code coverage information, and upload them to
  https://codecov.io/github/warner/magic-wormhole?ref=master .

## Release 0.6.3 (29-Feb-2016)

Mostly internal changes:

* twisted.transit was added, so Twisted-based applications can use it now.
  This includes Producer/Consumer -based flow control. The Transit protocol
  and API are documented in docs/transit.md .
* The transit relay server can blur filesizes, rounding them to some
  roughly-logarithmic interval.
* Use --relay-helper="" to disable use of the transit relay entirely,
  limiting the file transfer to direct connections.
* The new --hide-progress option disables the progress bar.
* Made some windows-compatibility fixes, but all tests do not yet pass.

## Release 0.6.2 (12-Jan-2016)

* the server can now "blur" usage information: this turns off HTTP logging,
  and rounds timestamps to coarse intervals
* `wormhole server usage` now shows Transit usage too, not just Rendezvous

## Release 0.6.1 (03-Dec-2015)

* `wormhole` can now send/receive entire directories. They are zipped before
  transport.
* Python 3 is now supported for async (Twisted) library use, requiring at
  least Twisted-15.5.0.
* A bug was fixed which prevented py3-based clients from using the relay
  transit server (not used if the two sides can reach each other directly).
* The `--output-file=` argument was finally implemented, which allows the
  receiver to override the filename that it writes. This may help scripted
  usage.
* Support for Python-2.6 was removed, since the recent Twisted-15.5.0 removed
  it too. It might still work, but is no longer automatically tested.
* The transit relay now implements proper flow control (Producer/Consumer),
  so it won't buffer the entire file when the sender can push data faster
  than the receiver can accept it. The sender should now throttle down to the
  receiver's maximum rate.

## Release 0.6.0 (23-Nov-2015)

* Add key-confirmation message so "wormhole send" doesn't hang when the
  receiver mistypes the code.
* Fix `wormhole send --text -` to read the text message from stdin. `wormhole
  receive >outfile` works, but currently appends an extra newline, which may
  be removed in a future release.
* Arrange for 0.4.0 senders to print an error message when connecting to a
  current (0.5.0) server, instead of an ugly stack trace. Unfortunately 0.4.0
  receivers still display the traceback, since they don't check the welcome
  message before using a missing API. 0.5.0 and 0.6.0 will do better.
* Improve channel deallocation upon error.
* Inform the server of our "mood" when the connection closes, so it can track
  the rate of successful/unsuccessful transfers. The server DB now stores a
  summary of each transfer (waiting time and reported outcome).
* Rename (and deprecate) one server API (the non-EventSource form of "get"),
  leaving it in place until after the next release. 0.5.0 clients should
  interoperate with both the 0.6.0 server and 0.6.0 clients, but eventually
  they'll stop working.

## Release 0.5.0 (07-Oct-2015)

* Change the CLI to merge send-file with send-text, and receive-file with
  receive-text. Add confirmation before accepting a file.
* Change the remote server API significantly, breaking compatibility with
  0.4.0 peers. Fix EventSource to match W3C spec and real browser behavior.
* Add py3 (3.3, 3.4, 3.5) compatibility for blocking calls (but not Twisted).
* internals
 * Introduce Channel and ChannelManager to factor out the HTTP/EventSource
   technology in use (making room for WebSocket or Tor in the future).
 * Change app-visible API to allow multiple message phases.
 * Change most API arguments from bytes to unicode strings (appid, URLs,
   wormhole code, derive_key purpose string, message phase). Derived keys are
   bytes, of course.
* Add proper unit tests.

## Release 0.4.0 (22-Sep-2015)

This changes the protocol (to a symmetric form), breaking compatibility with
0.3.0 peers. Now both blocking-style and Twisted-style use a symmetric
protocol, and the two sides do not need to figure out (ahead of time) which
one goes first. The internal layout was rearranged, so applications that
import wormhole must be updated.

## Release 0.3.0 (24-Jun-2015)

Add preliminary Twisted support, only for symmetric endpoints (no
initator/receiver distinction). Lacks code-entry tab-completion. May still
leave timers lingering. Add test suite (only for Twisted, so far).

Use a sqlite database for Relay server state, to survive reboots with less
data loss. Add "--advertise-version=" to "wormhole relay start", to override
the version we recommend to clients.

## Release 0.2.0 (10-Apr-2015)

Initial release: supports blocking/synchronous asymmetric endpoints
(Initiator on one side, Receiver on the other). Codes can be generated by
Initiator, or created externally and passed into both (as long as they start
with digits: NNN-anything).
