import sys
import wormhole
from wormhole.cli import public_relay
from twisted.internet.defer import ensureDeferred
from twisted.internet.task import react

# Run api-example.py in 2 different processes:
#   1 -- entering 0 arguments will initiate a wormhole send with a hidden msg
#   2 -- entering the sender's code will inititiate a wormhole receive

# The api can be set up to use a different relay like a websocket
# relay_url = "ws://localhost:4000/v1"


async def example_sender(reactor):
    """Our sender protocol is simple: send a message. After "versions" is exchanged
    the flow of messages is up to application requirements.
    """
    appid = "lothar.com/example"
    relay_url = public_relay.RENDEZVOUS_RELAY

    w = wormhole.create(appid, relay_url, reactor)
    w.allocate_code()

    code = await w.get_code()
    print(f"code: {code}")
    # another peer needs to consume the above code (thereby connecting)

    # once we get the "versions" message we have a viable
    # communication channel with shared encryption key
    versions = await w.get_versions()

    # our simple protocol is: the responder sends a single
    # message. the flow of messages after "versions" is exchanged is
    # up to application requirements
    w.send_message(b"privacy is a human right")
    result = await w.close()
    print(f"closed: {result}")


async def example_receiver(reactor, code):
    """Our receiver protocol is simple: complete a viable communication channel
    and wait for a message.
    """
    appid = "lothar.com/example"
    relay_url = public_relay.RENDEZVOUS_RELAY

    w = wormhole.create(appid, relay_url, reactor)
    w.set_code(code)

    # we can confirm a viable communication channel with "await
    # w.get_versions()" here but in this case we simply wait for the
    # other side to send its sole message; other protocols may choose
    # more complex arrangements.

    msg = await w.get_message()  # gets exactly one message
    print(f"got msg: {len(msg)} bytes")
    print(f"msg: {msg.decode("utf8")}")
    result = await w.close()
    print(f"closed: {result}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        react(lambda reactor: ensureDeferred(
            example_receiver(reactor, sys.argv[1])
        ))
    else:
        react(lambda reactor: ensureDeferred(
            example_sender(reactor)
        ))
