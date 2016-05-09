
User-visible changes in "magic-wormhole":

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
