import json

from twisted.internet.defer import inlineCallbacks

from . import wormhole
from .tor_manager import get_tor


@inlineCallbacks
def receive(reactor,
            appid,
            relay_url,
            code,
            use_tor=False,
            launch_tor=False,
            tor_control_port=None,
            on_code=None):
    """
    This is a convenience API which returns a Deferred that callbacks
    with a single chunk of data from another wormhole (and then closes
    the wormhole). Under the hood, it's just using an instance
    returned from :func:`wormhole.wormhole`. This is similar to the
    `wormhole receive` command.

    :param unicode appid: our application ID

    :param unicode relay_url: the relay URL to use

    :param unicode code: a pre-existing code to use, or None

    :param bool use_tor: True if we should use Tor, False to not use it (None
                         for default)

    :param on_code: if not None, this is called when we have a code (even if
                    you passed in one explicitly)
    :type on_code: single-argument callable
    """
    tor = None
    if use_tor:
        tor = yield get_tor(reactor, launch_tor, tor_control_port)
        # For now, block everything until Tor has started. Soon: launch
        # tor in parallel with everything else, make sure the Tor object
        # can lazy-provide an endpoint, and overlap the startup process
        # with the user handing off the wormhole code

    wh = wormhole.create(appid, relay_url, reactor, tor=tor)
    if code is None:
        wh.allocate_code()
        code = yield wh.get_code()
    else:
        wh.set_code(code)
    # we'll call this no matter what, even if you passed in a code --
    # maybe it should be only in the 'if' block above?
    if on_code:
        on_code(code)
    data = yield wh.get_message()
    data = json.loads(data.decode("utf-8"))
    offer = data.get('offer', None)
    if not offer:
        raise Exception("Do not understand response: {}".format(data))
    msg = None
    if 'message' in offer:
        msg = offer['message']
        wh.send_message(
            json.dumps({
                "answer": {
                    "message_ack": "ok"
                }
            }).encode("utf-8"))

    else:
        raise Exception("Unknown offer type: {}".format(offer.keys()))

    yield wh.close()
    return msg


@inlineCallbacks
def send(reactor,
         appid,
         relay_url,
         data,
         code,
         use_tor=False,
         launch_tor=False,
         tor_control_port=None,
         on_code=None):
    """
    This is a convenience API which returns a Deferred that callbacks
    after a single chunk of data has been sent to another
    wormhole. Under the hood, it's just using an instance returned
    from :func:`wormhole.wormhole`. This is similar to the `wormhole
    send` command.

    :param unicode appid: the application ID

    :param unicode relay_url: the relay URL to use

    :param unicode code: a pre-existing code to use, or None

    :param bool use_tor: True if we should use Tor, False to not use it (None
                         for default)

    :param on_code: if not None, this is called when we have a code (even if
                    you passed in one explicitly)

    :type on_code: single-argument callable
    """
    tor = None
    if use_tor:
        tor = yield get_tor(reactor, launch_tor, tor_control_port)
        # For now, block everything until Tor has started. Soon: launch
        # tor in parallel with everything else, make sure the Tor object
        # can lazy-provide an endpoint, and overlap the startup process
        # with the user handing off the wormhole code
    wh = wormhole.create(appid, relay_url, reactor, tor=tor)
    if code is None:
        wh.allocate_code()
        code = yield wh.get_code()
    else:
        wh.set_code(code)
    if on_code:
        on_code(code)

    wh.send_message(json.dumps({"offer": {"message": data}}).encode("utf-8"))
    data = yield wh.get_message()
    data = json.loads(data.decode("utf-8"))
    answer = data.get('answer', None)
    yield wh.close()
    if answer:
        return None
    else:
        raise Exception("Unknown answer: {}".format(data))
