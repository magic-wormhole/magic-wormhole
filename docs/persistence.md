# (somewhat-) Persistent Wormholes

Some applications (including the ``wormhole`` tool itself) work in a sort of
one-shot "succeed or die trying" mode, where all state is kept in RAM, and
any failure terminates the entire operation.

## Waterken Style

Other applications take a longer view. The "Waterken" style of checkpointed
state transitions is a particularly robust model for these kinds of
applications. In this style, all state is stored in a transactional database,
which guarantees that each new restart of the program will see the most
recent checkpoint: no regressions and no intermediate uncheckpointed state.
Programs are entirely reactive. When an inbound message arrives, the program
processes the message to compute two things: the set of new outbound messages
it wishes to send, and the new local state vector. It then commits both
values to the database. Once that succeeds, it actually sends the outbound
messages, and proceeds with its new state vector. Messages remain in the
outbound list until they are acknowledged by the recipient (who doesn't send
that ack until they've been committed locally), at which point they are
persistently removed.

If a crash occurs, the program will (potentially) re-transmit any unacked
outbound messages, so all messages must be idempotent. The program will also
re-process any inbound messages that had not made it to a checkpoint (since
they'll be re-transmitted by their senders), so the computation must be a
deterministic function of the state vector and the inbound messages.

## Persistent Wormholes

To use the Wormhole library from a persistent-style application, create the
Wormhole with ``persistent_wormhole()`` instead of the regular ``wormhole()``
function. This takes a ``store=`` argument, which is a function that will be
called each time the Wormhole wants to save its state (the argument will be a
JSON-serializable dictionary, and the function may return a Deferred if it
wants). It also takes a ``state=`` argument, with the dictionary that was
most recently passed to the ``store()`` function.

The Wormhole will replay all inbound messages. It will re-send outbound
messages. Do not (?) call wormhole.send() multiple times with the same
message, or it will get confused.

This also turns off the "deallocate upon disconnect" flag on server-side
mailboxes. Normally, when a client loses their websocket connection to the
server, it is because the application terminated unexpectedly, or the network
connection has been lost. The command-line ``wormhole`` tool does not yet
know how to resume such a connection, so the server automatically deletes the
mailbox to let others re-use the short channel id quickly.

In persistent mode, the mailbox is retained even when the connection is lost.
