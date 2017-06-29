# Journaled Mode

(note: this section is speculative, the code has not yet been written)

Magic-Wormhole supports applications which are written in a "journaled" or
"checkpointed" style. These apps store their entire state in a well-defined
checkpoint (perhaps in a database), and react to inbound events or messages
by carefully moving from one state to another, then releasing any outbound
messages. As a result, they can be terminated safely at any moment, without
warning, and ensure that the externally-visible behavior is deterministic and
independent of this stop/restart timing.

This is the style encouraged by the E event loop, the
original [Waterken Server](http://waterken.sourceforge.net/), and the more
modern [Ken Platform](http://web.eecs.umich.edu/~tpkelly/Ken/), all
influential in the object-capability security community.

## Requirements

Applications written in this style must follow some strict rules:

* all state goes into the checkpoint
* the only way to affect the state is by processing an input message
* event processing is deterministic (any non-determinism must be implemented
  as a message, e.g. from a clock service or a random-number generator)
* apps must never forget a message for which they've accepted reponsibility

The main processing function takes the previous state checkpoint and a single
input message, and produces a new state checkpoint and a set of output
messages. For performance, the state might be kept in memory between events,
but the behavior should be indistinguishable from that of a server which
terminates completely between events.

In general, applications must tolerate duplicate inbound messages, and should
re-send outbound messages until the recipient acknowledges them. Any outbound
responses to an inbound message must be queued until the checkpoint is
recorded. If outbound messages were delivered before the checkpointing, then
a crash just after delivery would roll the process back to a state where it
forgot about the inbound event, causing observably inconsistent behavior that
depends upon whether the outbound message successfully escaped the dying
process or not.

As a result, journaled-style applications use a very specific process when
interacting with the outside world. Their event-processing function looks
like:

* receive inbound event
* (load state)
* create queue for any outbound messages
* process message (changing state and queuing outbound messages)
* serialize state, record in checkpoint
* deliver any queued outbound messages

In addition, the protocols used to exchange messages should include message
IDs and acks. Part of the state vector will include a set of unacknowledged
outbound messages. When a connection is established, all outbound messages
should be re-sent, and messages are removed from the pending set when an
inbound ack is received. The state must include a set of inbound message ids
which have been processed already. All inbound messages receive an ack, but
only new ones are processed. Connection establishment/loss is not strictly
included in the journaled-app model (in Waterken/Ken, message delivery is
provided by the platform, and apps do not know about connections), but
general:

* "I want to have a connection" is stored in the state vector
* "I am connected" is not
* when a connection is established, code can run to deliver pending messages,
  and this does not qualify as an inbound event
* inbound events can only happen when at least one connection is established
* immediately after restarting from a checkpoint, no connections are
  established, but the app might initiate outbound connections, or prepare to
  accept inbound ones

## Wormhole Support

To support this mode, the Wormhole constructor accepts a `journal=` argument.
If provided, it must be an object that implements the `wormhole.IJournal`
interface, which consists of two methods:

* `j.queue_outbound(fn, *args, **kwargs)`: used to delay delivery of outbound
  messages until the checkpoint has been recorded
* `j.process()`: a context manager which should be entered before processing
  inbound messages

`wormhole.Journal` is an implementation of this interface, which is
constructed with a (synchronous) `save_checkpoint` function. Applications can
use it, or bring their own.

The Wormhole object, when configured with a journal, will wrap all inbound
WebSocket message processing with the `j.process()` context manager, and will
deliver all outbound messages through `j.queue_outbound`. Applications using
such a Wormhole must also use the same journal for their own (non-wormhole)
events. It is important to coordinate multiple sources of events: e.g. a UI
event may cause the application to call `w.send(data)`, and the outbound
wormhole message should be checkpointed along with the app's state changes
caused by the UI event. Using a shared journal for both wormhole- and
non-wormhole- events provides this coordination.

The `save_checkpoint` function should serialize application state along with
any Wormholes that are active. Wormhole state can be obtained by calling
`w.serialize()`, which will return a dictionary (that can be
JSON-serialized). At application startup (or checkpoint resumption),
Wormholes can be regenerated with `wormhole.from_serialized()`. Note that
only "delegated-mode" wormholes can be serialized: Deferreds are not amenable
to usage beyond a single process lifetime.

For a functioning example of a journaled-mode application, see
misc/demo-journal.py. The following snippet may help illustrate the concepts:

```python
class App:
    @classmethod
    def new(klass):
        self = klass()
        self.state = {}
        self.j = wormhole.Journal(self.save_checkpoint)
        self.w = wormhole.create(.., delegate=self, journal=self.j)

    @classmethod
    def from_serialized(klass):
        self = klass()
        self.j = wormhole.Journal(self.save_checkpoint)
        with open("state.json", "r") as f:
            data = json.load(f)
        self.state = data["state"]
        self.w = wormhole.from_serialized(data["wormhole"], reactor,
                                          delegate=self, journal=self.j)

    def inbound_event(self, event):
        # non-wormhole events must be performed in the journal context
        with self.j.process():
            parse_event(event)
            change_state()
            self.j.queue_outbound(self.send, outbound_message)

    def wormhole_received(self, data):
        # wormhole events are already performed in the journal context
        change_state()
        self.j.queue_outbound(self.send, stuff)

    def send(self, outbound_message):
        actually_send_message(outbound_message)

    def save_checkpoint(self):
        app_state = {"state": self.state, "wormhole": self.w.serialize()}
        with open("state.json", "w") as f:
            json.dump(app_state, f)
```
