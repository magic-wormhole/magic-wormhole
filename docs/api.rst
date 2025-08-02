The Magic-Wormhole API
======================

This library provides a mechanism to securely transfer small amounts of
data between two computers. Both machines must be connected to the
internet, but they do not need to have public IP addresses or know how
to contact each other ahead of time.

Security and connectivity is provided by means of a “wormhole code”: a
short string that is transcribed from one machine to the other by the
users at the keyboard. This works in conjunction with a baked-in
“mailbox server” that relays information from one machine to the other.

The “Wormhole” object provides a secure record pipe between any two
programs that use the same wormhole code (and are configured with the
same application ID and mailbox server). Each side can send multiple
messages to the other, but the encrypted data for all messages must pass
through (and be temporarily stored on) the mailbox server, which is a
shared resource. For this reason, larger data (including bulk file
transfers) should use the Transit class instead. The Wormhole can be
used to create a Transit object for this purpose. In the future, Transit
will be deprecated, and this functionality will be incorporated directly
as a “dilated wormhole”.

A quick example:

.. code:: python

   import wormhole

   async def example_initiator(reactor):
       w = wormhole.create(appid, relay_url, reactor)
       w.allocate_code()

       code = await w.get_code()
       print(f"code: {code}")

       w.send_message(b"outbound data")
       inbound = await w.get_message()
       await w.close()

Modes
-----

The API comes in two flavors: Delegated and Deferred. Controlling the
Wormhole and sending data is identical in both, but they differ in how
inbound data and events are delivered to the application.

In Delegated mode, the Wormhole is given a “delegate” object, on which
certain methods will be called when information is available (e.g. when
the code is established, or when data messages are received). In
Deferred mode, the Wormhole object has methods which return Deferreds
that will fire at these same times.

Delegated mode:

.. code:: python

   class MyDelegate:
       def wormhole_got_code(self, code):
           print("code: %s" % code)
       def wormhole_got_message(self, msg): # called for each message
           print("got data, %d bytes" % len(msg))

   w = wormhole.create(appid, relay_url, reactor, delegate=MyDelegate())
   w.allocate_code()

Deferred mode:

.. code:: python

   async def example_initiator(reactor):
       appid = "lothar.com/example"
       relay_url = public_relay.MAILBOX_RELAY
       w = wormhole.create(appid, relay_url, reactor)
       w.allocate_code()

       code = await w.get_code()
       print(f"code: {code}")

       msg = await w.get_message() # gets exactly one message
       print(f"got msg: {len(msg)} bytes")
       result = await w.close()
       print(f"closed: {result}")

Application Identifier
----------------------

Applications using this library must provide an “application
identifier”, a simple string that distinguishes one application from
another. To ensure uniqueness, use a domain name. To use multiple apps
for a single domain, append a URL-like slash and path, like
``example.com/app1``. This string must be the same on both clients,
otherwise they will not see each other. The invitation codes are scoped
to the app-id. Note that the app-id must be unicode, not bytes.

Distinct app-ids reduce the size of the connection-id numbers. If fewer
than ten Wormholes are active for a given app-id, the connection-id will
only need to contain a single digit, even if some other app-id is
currently using thousands of concurrent sessions.

Mailbox Servers
---------------

The library depends upon a “mailbox server” which is a service (on a
public IP address) that delivers small encrypted messages from one
client to the other. This must be the same for both clients, and is
generally baked-in to the application source code or default config.

This library includes the URL of a public mailbox server run by the
author. Application developers can use this one, or they can run their
own (see the
`magic-wormhole/magic-wormhole-mailbox-server <https://github.com/magic-wormhole/magic-wormhole-mailbox-server>`__
repository) and configure their clients to use it instead. The URL of
the public mailbox server is passed as a unicode string. Note that
because the server actually speaks WebSockets, the URL starts with
``ws:`` (or ``wss:``) instead of ``http:``.

Wormhole Parameters
-------------------

All wormholes must be created with at least three parameters:

-  ``appid``: a (unicode) string
-  ``relay_url``: a (unicode) string
-  ``reactor``: the Twisted reactor object

In addition to these three, the ``wormhole.create()`` function takes
several optional arguments:

-  ``delegate``: provide a Delegate object to enable “delegated mode”,
   or pass None (the default) to get “deferred mode”
-  ``journal``: provide a Journal object to enable journaled mode. See
   :doc:`the journal docs <journal>` for details. Note that journals
   only work with delegated mode, not with deferred mode.
-  ``tor_manager``: to enable Tor support, create a
   ``wormhole.TorManager`` instance and pass it here. This will hide the
   client’s IP address by proxying all connections (mailbox and transit)
   through Tor. It also enables connecting to Onion-service transit
   hints, and (in the future) will enable the creation of Onion-services
   for transit purposes.
-  ``timing``: this accepts a DebugTiming instance, mostly for internal
   diagnostic purposes, to record the transmit/receive timestamps for
   all messages. The ``wormhole --dump-timing=`` feature uses this to
   build a JSON-format data bundle, and the ``misc/dump-timing.py`` tool
   can build a scrollable timing diagram from these bundles.
-  ``welcome_handler``: this is a function that will be called when the
   Mailbox Server’s “welcome” message is received. It is used to display
   important server messages in an application-specific way.
-  ``versions``: this can accept a dictionary (JSON-encodable) of data
   that will be made available to the peer via the ``got_version``
   event. This data is delivered before any data messages, and can be
   used to indicate peer capabilities.
- ``on_status_update``: this single-argument callable will receive
   instances of ``wormhole.WormholeStatus`` as the status of our
   wormhole changes; useful to show your users


Code Management
---------------

Each wormhole connection is defined by a shared secret “wormhole code”.
These codes can be created by humans offline (by picking a unique number
and some secret words), but are more commonly generated by asking the
library to make one. In the “bin/wormhole” file-transfer tool, the
default behavior is for the sender’s program to create the code, and for
the receiver to type it in.

The code is a (unicode) string in the form ``NNN-code-words``. The
numeric “NNN” prefix is the “channel id” or “nameplate”, and is a short
integer allocated by talking to the mailbox server. The rest is a
randomly-generated selection from the PGP wordlist, providing a default
of 16 bits of entropy. The initiating program should display this code
to the user, who should transcribe it to the receiving user, who gives
it to their local Wormhole object by calling ``set_code()``. The
receiving program can also use ``input_code()`` to use a readline-based
input function: this offers tab completion of allocated channel-ids and
known codewords.

The Wormhole object has three APIs for generating or accepting a code:

-  ``w.allocate_code(length=2)``: this contacts the Mailbox Server,
   allocates a short numeric nameplate, chooses a configurable number of
   random words, then assembles them into the code
-  ``w.set_code(code)``: this accepts the complete code as an argument
-  ``helper = w.input_code()``: this facilitates interactive entry of
   the code, with tab-completion. The helper object has methods to
   return a list of viable completions for whatever portion of the code
   has been entered so far. A convenience wrapper is provided to attach
   this to the ``rlcompleter`` function of libreadline.

No matter which mode is used, the ``w.get_code()`` Deferred (or
``delegate.wormhole_got_code(code)`` callback) will fire when the code
is known. ``get_code`` is clearly necessary for ``allocate_code``, since
there’s no other way to learn what code was created, but it may be
useful in other modes for consistency.

The code-entry Helper object has the following API:

-  ``refresh_nameplates()``: requests an updated list of nameplates from
   the Mailbox Server. These form the first portion of the wormhole code
   (e.g. “4” in “4-purple-sausages”). Note that they are unicode strings
   (so “4”, not 4). The Helper will get the response in the background,
   and calls to ``get_nameplate_completions()`` after the response will
   use the new list. Calling this after ``h.choose_nameplate`` will
   raise ``AlreadyChoseNameplateError``.
-  ``matches = h.get_nameplate_completions(prefix)``: returns
   (synchronously) a set of completions for the given nameplate prefix,
   along with the hyphen that always follows the nameplate (and
   separates the nameplate from the rest of the code). For example, if
   the server reports nameplates 1, 12, 13, 24, and 170 are in use,
   ``get_nameplate_completions("1")`` will return
   ``{"1-", "12-", "13-", "170-"}``. You may want to sort these before
   displaying them to the user. Raises ``AlreadyChoseNameplateError`` if
   called after ``h.choose_nameplate``.
-  ``h.choose_nameplate(nameplate)``: accepts a string with the chosen
   nameplate. May only be called once, after which
   ``AlreadyChoseNameplateError`` is raised. (in this future, this might
   return a Deferred that fires (with None) when the nameplate’s
   wordlist is known (which happens after the nameplate is claimed,
   requiring a roundtrip to the server)).
-  ``d = h.when_wordlist_is_available()``: return a Deferred that fires
   (with None) when the wordlist is known. This can be used to block a
   readline frontend which has just called ``h.choose_nameplate()``
   until the resulting wordlist is known, which can improve the
   tab-completion behavior.
-  ``matches = h.get_word_completions(prefix)``: return (synchronously)
   a set of completions for the given words prefix. This will include a
   trailing hyphen if more words are expected. The possible completions
   depend upon the wordlist in use for the previously-claimed nameplate,
   so calling this before ``choose_nameplate`` will raise
   ``MustChooseNameplateFirstError``. Calling this after
   ``h.choose_words()`` will raise ``AlreadyChoseWordsError``. Given a
   prefix like “su”, this returns a set of strings which are potential
   matches (e.g. ``{"supportive-", "surrender-", "suspicious-"}``. The
   prefix should not include the nameplate, but *should* include
   whatever words and hyphens have been typed so far (the default
   wordlist uses alternate lists, where even numbered words have three
   syllables, and odd numbered words have two, so the completions depend
   upon how many words are present, not just the partial last word).
   E.g. ``get_word_completions("pr")`` will return
   ``{"processor-", "provincial-", "proximate-"}``, while
   ``get_word_completions("opulent-pr")`` will return
   ``{"opulent-preclude",   "opulent-prefer", "opulent-preshrunk", "opulent-printer",   "opulent-prowler"}``
   (note the lack of a trailing hyphen, because the wordlist is
   expecting a code of length two). If the wordlist is not yet known,
   this returns an empty set. All return values will
   ``.startswith(prefix)``. The frontend is responsible for sorting the
   results before display.
-  ``h.choose_words(words)``: call this when the user is finished typing
   in the code. It does not return anything, but will cause the
   Wormhole’s ``w.get_code()`` (or corresponding delegate) to fire, and
   triggers the wormhole connection process. This accepts a string like
   “purple-sausages”, without the nameplate. It must be called after
   ``h.choose_nameplate()`` or ``MustChooseNameplateFirstError`` will be
   raised. May only be called once, after which
   ``AlreadyChoseWordsError`` is raised.

The ``input_with_completion`` wrapper is a function that knows how to
use the code-entry helper to do tab completion of wormhole codes:

.. code:: python

   from wormhole import create, input_with_completion

   async def example(reactor):
       w = create(appid, relay_url, reactor)
       input_with_completion("Wormhole code:", w.input_code(), reactor)
       code = await w.get_code()

This helper runs python’s (raw) ``input()`` function inside a thread,
since ``input()`` normally blocks.

The two machines participating in the wormhole setup are not
distinguished: it doesn’t matter which one goes first, and both use the
same Wormhole constructor function. However if ``w.allocate_code()`` is
used, only one side should use it.

Providing an invalid nameplate (which is easily caused by cut-and-paste
errors that include an extra space at the beginning, or which copy the
words but not the number) will raise a ``KeyFormatError``, either in
``w.set_code(code)`` or in ``h.choose_nameplate()``.

Offline Codes
-------------

In most situations, the “sending” or “initiating” side will call
``w.allocate_code()`` and display the resulting code. The sending human
reads it and speaks, types, performs charades, or otherwise transmits
the code to the receiving human. The receiving human then types it into
the receiving computer, where it either calls ``w.set_code()`` (if the
code is passed in via argv) or ``w.input_code()`` (for interactive
entry).

Usually one machine generates the code, and a pair of humans transcribes
it to the second machine (so ``w.allocate_code()`` on one side, and
``w.set_code()`` or ``w.input_code()`` on the other). But it is also
possible for the humans to generate the code offline, perhaps at a
face-to-face meeting, and then take the code back to their computers. In
this case, ``w.set_code()`` will be used on both sides. It is unlikely
that the humans will restrict themselves to a pre-established wordlist
when manually generating codes, so the completion feature of
``w.input_code()`` is not helpful.

When the humans create an invitation code out-of-band, they are
responsible for choosing an unused channel-ID (simply picking a random
3-or-more digit number is probably enough), and some random words. Dice,
coin flips, shuffled cards, or repeated sampling of a high-resolution
stopwatch are all useful techniques. The invitation code uses the same
format either way: channel-ID, a hyphen, and an arbitrary string. There
is no need to encode the sampled random values (e.g. by using the
Diceware wordlist) unless that makes it easier to transcribe:
e.g. rolling 6 dice could result in a code like “913-166532”, and
flipping 16 coins could result in “123-HTTHHHTTHTTHHTHH”.

Welcome Messages
----------------

The first message sent by the mailbox server is a “welcome” message (a
dictionary). This is sent as soon as the client connects to the server,
generally before the code is established. Clients should use
``await get_welcome()`` to get and process the ``motd`` key (and maybe
``current_cli_version``) inside the welcome message.

The welcome message serves three main purposes:

-  notify users about important server changes, such as CAPTCHA
   requirements driven by overload, or donation requests
-  enable future protocol negotiation between clients and the server
-  advise users of the CLI tools (``wormhole send``) to upgrade to a new
   version

There are three keys currently defined for the welcome message, all of
which are optional (the welcome message omits “error” and “motd” unless
the server operator needs to signal a problem).

-  ``motd``: if this key is present, it will be a string with embedded
   newlines. The client should display this string to the user,
   including a note that it comes from the magic-wormhole Mailbox Server
   and that server’s URL.
-  ``error``: if present, the server has decided it cannot service this
   client. The string will be wrapped in a ``WelcomeError`` (which is a
   subclass of ``WormholeError``), and all API calls will signal errors
   (pending Deferreds will errback). The mailbox connection will be
   closed.
-  ``current_cli_version``: if present, the server is advising instances
   of the CLI tools (the ``wormhole`` command included in the python
   distribution) that there is a newer release available, thus users
   should upgrade if they can, because more features will be available
   if both clients are running the same version. The CLI tools compare
   this string against their ``__version__`` and can print a short
   message to stderr if an upgrade is warranted.

The main idea of ``error`` is to allow the server to cleanly inform the
client about some necessary action it didn’t take. The server currently
sends the welcome message as soon as the client connects (even before it
receives the “claim” request), but a future server could wait for a
required client message and signal an error (via the Welcome message) if
it didn’t see this extra message before the CLAIM arrived.

This could enable changes to the protocol, e.g. requiring a CAPTCHA or
proof-of-work token when the server is under DoS attack. The new server
would send the current requirements in an initial message (which old
clients would ignore). New clients would be required to send the token
before their “claim” message. If the server sees “claim” before “token”,
it knows that the client is too old to know about this protocol, and it
could send a “welcome” with an ``error`` field containing instructions
(explaining to the user that the server is under attack, and they must
either upgrade to a client that can speak the new protocol, or wait
until the attack has passed). Either case is better than an opaque
exception later when the required message fails to arrive.

(Note that the server can also send an explicit ERROR message at any
time, and the client should react with a ServerError. Versions 0.9.2 and
earlier of the library did not pay attention to the ERROR message, hence
the server should deliver errors in a WELCOME message if at all
possible)

The ``error`` field is handled internally by the Wormhole object. The
other fields can be processed by application, by using
``d=w.get_welcome()``. The Deferred will fire with the full welcome
dictionary, so any other keys that a future server might send will be
available to it.

Applications which need to display ``motd`` or an upgrade message, and
wish to do so before using stdin/stdout for interactive code entry
(``w.input_code()``) should wait for ``get_welcome()`` before starting
the entry process:

.. code:: python

   async def go():
       w = wormhole.create(appid, relay_url, reactor)
       welcome = await w.get_welcome()
       if "motd" in welcome:
           print(welcome["motd"])
       input_with_completion("Wormhole code:", w.input_code(), reactor)
       ...

Verifier
--------

For extra protection against guessing attacks, Wormhole can provide a
“Verifier”. This is a moderate-length series of bytes (a SHA256 hash)
that is derived from the supposedly-shared session key. If desired, both
sides can display this value, and the humans can manually compare them
before allowing the rest of the protocol to proceed. If they do not
match, then the two programs are not talking to each other (they may
both be talking to a man-in-the-middle attacker), and the protocol
should be abandoned.

Deferred-mode applications can wait for ``d=w.get_verifier()``: the
Deferred it returns will fire with the verifier. You can turn this into
hex or Base64 to print it, or render it as ASCII-art, etc.

Asking the wormhole object for the verifier does not affect the flow of
the protocol. To benefit from verification, applications must refrain
from sending any data (with ``w.send_message(data)``) until after the
verifiers are approved by the user. In addition, applications must queue
or otherwise ignore incoming (received) messages until that point.
However once the verifiers are confirmed, previously-received messages
can be considered valid and processed as usual.

Events
------

As the wormhole connection is established, several events may be
dispatched to the application. In Delegated mode, these are dispatched
by calling functions on the delegate object. In Deferred mode, the
application retrieves Deferred objects from the wormhole, and event
dispatch is performed by firing those Deferreds.

Most applications will only use ``code``, ``received``, and ``close``.

-  code (``code = yield w.get_code()`` /
   ``dg.wormhole_got_code(code)``): fired when the wormhole code is
   established, either after ``w.allocate_code()`` finishes the
   generation process, or when the Input Helper returned by
   ``w.input_code()`` has been told ``h.set_words()``, or immediately
   after ``w.set_code(code)`` is called. This is most useful after
   calling ``w.allocate_code()``, to show the generated code to the user
   so they can transcribe it to their peer.
-  key (``yield w.get_unverified_key()`` /
   ``dg.wormhole_got_unverified_key(key)``): fired (with the raw master
   SPAKE2 key) when the key-exchange process has completed and a
   purported shared key is established. At this point we do not know
   that anyone else actually shares this key: the peer may have used the
   wrong code, or may have disappeared altogether. To wait for proof
   that the key is shared, wait for ``get_verifier`` instead. This event
   is really only useful for detecting that the initiating peer has
   disconnected after leaving the initial PAKE message, to display a
   pacifying message to the user.
-  verifier (``verifier = yield w.get_verifier()`` /
   ``dg.wormhole_got_verifier(verifier)``: fired when the key-exchange
   process has completed and a valid VERSION message has arrived. The
   “verifier” is a byte string with a hash of the shared session key;
   clients can compare them (probably as hex) to ensure that they’re
   really talking to each other, and not to a man-in-the-middle. When
   ``get_verifier`` happens, this side knows that *someone* has used the
   correct wormhole code; if someone used the wrong code, the VERSION
   message cannot be decrypted, and the wormhole will be closed instead.
-  versions (``versions = yield w.get_versions()`` /
   ``dg.wormhole_got_versions(versions)``: fired when the VERSION
   message arrives from the peer. This fires just after ``verified``,
   but delivers the “app_versions” data (as passed into
   ``wormhole.create(versions=)``) instead of the verifier string. This
   is mostly a hack to make room for forwards-compatible changes to the
   CLI file-transfer protocol, which sends a request in the first
   message (rather than merely sending the abilities of each side).
-  received (``yield w.get_message()`` /
   ``dg.wormhole_got_message(msg)``: fired each time a data message
   arrives from the peer, with the bytestring that the peer passed into
   ``w.send_message(msg)``. This is the primary data-transfer API.
-  closed (``yield w.close()`` / ``dg.wormhole_closed(result)``: fired
   when ``w.close()`` has finished shutting down the wormhole, which
   means all nameplates and mailboxes have been deallocated, and the
   WebSocket connection has been closed. This also fires if an internal
   error occurs (specifically WrongPasswordError, which indicates that
   an invalid encrypted message was received), which also shuts
   everything down. The ``result`` value is an exception (or Failure)
   object if the wormhole closed badly, or a string like “happy” if it
   had no problems before shutdown.

Sending Data
------------

The main purpose of a Wormhole is to send data. At any point after
construction, callers can invoke ``w.send_message(msg)``. This will
queue the message if necessary, but (if all goes well) will eventually
result in the peer getting a ``received`` event and the data being
delivered to the application.

Since Wormhole provides an ordered record pipe, each call to
``w.send_message`` will result in exactly one ``received`` event on the
far side. Records are not split, merged, dropped, or reordered.

Each side can do an arbitrary number of ``send_message()`` calls. The
Wormhole is not meant as a long-term communication channel, but some
protocols work better if they can exchange an initial pair of messages
(perhaps offering some set of negotiable capabilities), and then follow
up with a second pair (to reveal the results of the negotiation). The
Mailbox Server does not currently enforce any particular limits on
number of messages, size of messages, or rate of transmission, but in
general clients are expected to send fewer than a dozen messages, of no
more than perhaps 20kB in size (remember that all these messages are
temporarily stored in a SQLite database on the server). A future version
of the protocol may make these limits more explicit, and will allow
clients to ask for greater capacity when they connect (probably by
passing additional “mailbox attribute” parameters with the
``allocate``/``claim``/``open`` messages).

For bulk data transfer, see :doc:`the transit docs <transit>` ,
or the “Dilation” section below.

Closing
-------

When the application is done with the wormhole, it should call
``w.close()``, and wait for a ``closed`` event. This ensures that all
server-side resources are released (allowing the nameplate to be reused
by some other client), and all network sockets are shut down.

In Deferred mode, this just means waiting for the Deferred returned by
``w.close()`` to fire. In Delegated mode, this means calling
``w.close()`` (which doesn’t return anything) and waiting for the
delegate’s ``wormhole_closed()`` method to be called.

``w.close()`` will errback (with some form of ``WormholeError``) if
anything went wrong with the process, such as:

-  ``WelcomeError``: the server told us to signal an error, probably
   because the client is too old understand some new protocol feature
-  ``ServerError``: the server rejected something we did
-  ``LonelyError``: we didn’t hear from the other side, so no key was
   established
-  ``WrongPasswordError``: we received at least one
   incorrectly-encrypted message. This probably indicates that the other
   side used a different wormhole code than we did, perhaps because of a
   typo, or maybe an attacker tried to guess your code and failed.

If the wormhole was happy at the time it was closed, the ``w.close()``
Deferred will callback (probably with the string “happy”, but this may
change in the future).

Serialization
-------------

(NOTE: this section is speculative: this code has not yet been written)

Wormhole objects can be serialized. This can be useful for apps which
save their own state before shutdown, and restore it when they next
start up again.

The ``w.serialize()`` method returns a dictionary which can be JSON
encoded into a unicode string (most applications will probably want to
UTF-8 -encode this into a bytestring before saving on disk somewhere).

To restore a Wormhole, call
``wormhole.from_serialized(data, reactor, delegate)``. This will return
a wormhole in roughly the same state as was serialized (of course all
the network connections will be disconnected).

Serialization only works for delegated-mode wormholes (since Deferreds
point at functions, which cannot be serialized easily). It also only
works for “non-dilated” wormholes (see below).

To ensure correct behavior, serialization should probably only be done
in “journaled mode”. See :doc:`the journal docs <journal>` for details.

If you use serialization, be careful to never use the same partial
wormhole object twice.

Dilation
--------

(NOTE: this API is still in development)

To send bulk data, or anything more than a handful of messages, a
Wormhole can be “dilated” into a form that uses a direct TCP connection
between the two endpoints.

All wormholes start out “undilated”. In this state, all messages are
queued on the Mailbox Server for the lifetime of the wormhole, and
server-imposed number/size/rate limits apply. Calling ``w.dilate()``
initiates the dilation process, and eventually yields a set of
Endpoints. Once dilated these endpoints can be used to establish
multiple (encrypted) “subchannel” connections to the other side.

You may pass an ``on_status_update`` callable to ``dilate()``, which
is a function that will be called with ``wormhole.DilationStatus``
instances whenever the status of the Dilation connection (or the
associated mailbox connection) changes.

Each subchannel behaves like a regular Twisted ``ITransport``, so they
can be glued to the Protocol instance of your choice. They also
implement the IConsumer/IProducer interfaces.

These subchannels are *durable*: as long as the processes on both sides
keep running, the subchannel will survive the network connection being
dropped. For example, a file transfer can be started from a laptop, then
while it is running, the laptop can be closed, moved to a new wifi
network, opened back up, and the transfer will resume from the new IP
address.

What’s good about a non-dilated wormhole?:

-  setup is faster: no delay while it tries to make a direct connection
-  works with “journaled mode”, allowing progress to be made even when
   both sides are never online at the same time, by serializing the
   wormhole

What’s good about dilated wormholes?:

-  they support bulk data transfer
-  you get flow control (backpressure), and IProducer/IConsumer
-  throughput is faster: no store-and-forward step

Use non-dilated wormholes when your application only needs to exchange a
couple of messages, for example to set up public keys or provision
access tokens. Use a dilated wormhole to move files, stream data, etc

Dilated wormholes can provide multiple “subchannels”: these are
multiplexed through the single (encrypted) TCP connection. Each
subchannel is a separate stream (offering IProducer/IConsumer for flow
control), and is opened and closed independently. A special “control
channel” is available to both sides so they can coordinate how they use
the subchannels.

The ``d = w.dilate()`` Deferred fires with a triple of Endpoints:

.. code:: python

   d = w.dilate()
   def _dilated(res):
       (control_channel_ep, subchannel_client_ep, subchannel_server_ep) = res
   d.addCallback(_dilated)

The ``control_channel_ep`` endpoint is a client-style endpoint, so both
sides will connect to it with ``ep.connect(factory)``. This endpoint is
single-use: calling ``.connect()`` a second time will fail. The control
channel is symmetric: it doesn’t matter which side is the
application-level client/server or initiator/responder, if the
application even has such concepts. The two applications can use the
control channel to negotiate who goes first, if necessary.

The subchannel endpoints are *not* symmetric: for each subchannel, one
side must listen as a server, and the other must connect as a client.
Subchannels can be established by either side at any time. This supports
e.g. bidirectional file transfer, where either user of a GUI app can
drop files into the “wormhole” whenever they like.

The ``subchannel_client_ep`` on one side is used to connect to the other
side’s ``subchannel_server_ep``, and vice versa. The client endpoint is
reusable. The server endpoint is single-use: ``.listen(factory)`` may
only be called once.

Applications are under no obligation to use subchannels: for many use
cases, the control channel is enough.

To use subchannels, once the wormhole is dilated and the endpoints are
available, the listening-side application should attach a listener to
the ``subchannel_server_ep`` endpoint:

.. code:: python

   def _dilated(res):
       (control_channel_ep, subchannel_client_ep, subchannel_server_ep) = res
       f = Factory(MyListeningProtocol)
       subchannel_server_ep.listen(f)

When the connecting-side application wants to connect to that listening
protocol, it should use ``.connect()`` with a suitable connecting
protocol factory:

.. code:: python

   def _connect():
       f = Factory(MyConnectingProtocol)
       subchannel_client_ep.connect(f)

For a bidirectional file-transfer application, both sides will establish
a listening protocol. Later, if/when the user drops a file on the
application window, that side will initiate a connection, use the
resulting subchannel to transfer the single file, and then close the
subchannel.

.. code:: python

   def FileSendingProtocol(internet.Protocol):
       def __init__(self, metadata, filename):
           self.file_metadata = metadata
           self.file_name = filename
       def connectionMade(self):
           self.transport.write(self.file_metadata)
           sender = protocols.basic.FileSender()
           f = open(self.file_name,"rb")
           d = sender.beginFileTransfer(f, self.transport)
           d.addBoth(self._done, f)
       def _done(res, f):
           self.transport.loseConnection()
           f.close()
   def _send(metadata, filename):
       f = protocol.ClientCreator(reactor,
                                  FileSendingProtocol, metadata, filename)
       subchannel_client_ep.connect(f)
   def FileReceivingProtocol(internet.Protocol):
       state = INITIAL
       def dataReceived(self, data):
           if state == INITIAL:
               self.state = DATA
               metadata = parse(data)
               self.f = open(metadata.filename, "wb")
           else:
               # local file writes are blocking, so don't bother with IConsumer
               self.f.write(data)
       def connectionLost(self, reason):
           self.f.close()
   def _dilated(res):
       (control_channel_ep, subchannel_client_ep, subchannel_server_ep) = res
       f = Factory(FileReceivingProtocol)
       subchannel_server_ep.listen(f)

Bytes, Strings, Unicode, and Python 3
-------------------------------------

All cryptographically-sensitive parameters are passed as bytes (“bytes” in
python3):

-  verifier string
-  data in/out
-  transit records in/out

Other (human-facing) values are always unicode (“str” in python3):

-  wormhole code
-  relay URL
-  transit URLs
-  transit connection hints (e.g. “host:port”)
-  application identifier
-  derived-key “purpose” string: ``w.derive_key(PURPOSE, LENGTH)``

Full API list
-------------

+----------------------+----------------------+----------------------+
| action               | Deferred-Mode        | Delegated-Mode       |
+======================+======================+======================+
| .                    | d=w.get_welcome()    | dg.wormhole_         |
|                      |                      | got_welcome(welcome) |
+----------------------+----------------------+----------------------+
| w.allocate_code()    |                      |                      |
+----------------------+----------------------+----------------------+
| h=w.input_code()     |                      |                      |
+----------------------+----------------------+----------------------+
| w.set_code(code)     |                      |                      |
+----------------------+----------------------+----------------------+
| .                    | d=w.get_code()       | dg.wor               |
|                      |                      | mhole_got_code(code) |
+----------------------+----------------------+----------------------+
| .                    | d=w.                 | dg.wormhole_got      |
|                      | get_unverified_key() | _unverified_key(key) |
+----------------------+----------------------+----------------------+
| .                    | d=w.get_verifier()   | dg.wormhole_go       |
|                      |                      | t_verifier(verifier) |
+----------------------+----------------------+----------------------+
| .                    | d=w.get_versions()   | dg.wormhole_go       |
|                      |                      | t_versions(versions) |
+----------------------+----------------------+----------------------+
| key=w                |                      |                      |
| .derive_key(purpose, |                      |                      |
| length)              |                      |                      |
+----------------------+----------------------+----------------------+
| w.send_message(msg)  |                      |                      |
+----------------------+----------------------+----------------------+
| .                    | d=w.get_message()    | dg.wormh             |
|                      |                      | ole_got_message(msg) |
+----------------------+----------------------+----------------------+
| w.close()            |                      | dg.wor               |
|                      |                      | mhole_closed(result) |
+----------------------+----------------------+----------------------+
| .                    | d=w.close()          |                      |
+----------------------+----------------------+----------------------+
