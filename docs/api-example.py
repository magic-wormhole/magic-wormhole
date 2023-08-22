import sys
import wormhole
from wormhole.cli import public_relay
from twisted.internet.defer import ensureDeferred
from twisted.internet.task import react


async def go():
    w = wormhole.create(appid, relay_url, reactor)
    w.allocate_code()
    code = await w.get_code()
    print(f"code: {code}")
    w.send_message(b"outbound data")
    inbound = await w.get_message()
    await w.close()


async def example_initiator(reactor):
    appid = "lothar.com/example"
    relay_url = public_relay.RENDEZVOUS_RELAY
    relay_url = "ws://localhost:4000/v1"
    w = wormhole.create(appid, relay_url, reactor)
    w.allocate_code()

    code = await w.get_code()
    print(f"code: {code}")

    # another peer needs to consume the above code (thereby
    # connecting) and send a single message before the below will
    # proceed

    # we can confirm a viable communiction channel with "await
    # w.get_versions()" here but in this case we simply wait for the
    # other side to send its sole message; other protocols may choose
    # more complex arrangements.

    msg = await w.get_message() # gets exactly one message
    print(f"got msg: {len(msg)} bytes")
    result = await w.close()
    print(f"closed: {result}")


async def example_responder(reactor, code):
    appid = "lothar.com/example"
    relay_url = public_relay.RENDEZVOUS_RELAY
    relay_url = "ws://localhost:4000/v1"
    w = wormhole.create(appid, relay_url, reactor)
    w.set_code(code)

    # once we get the "versions" message we have a viable
    # communication channel with shared encryption key
    versions = await w.get_versions()

    # our simple protocol is: the responder sends a single
    # message. the flow of messgaes after "versions" is exchanged is
    # up to application requirements
    w.send_message(b"privacy is a human right")
    result = await w.close()
    print(f"closed: {result}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        react(lambda reactor: ensureDeferred(
            example_responder(reactor, sys.argv[1])
        ))
    else:
        react(lambda reactor: ensureDeferred(
            example_initiator(reactor)
        ))
