from zope.interface import Interface

# These interfaces are private: we use them as markers to detect
# swapped argument bugs in the various .wire() calls

class IWormhole(Interface):
    """Internal: this contains the methods invoked 'from below'."""
    def got_welcome(welcome):
        pass
    def got_code(code):
        pass
    def got_key(key):
        pass
    def got_verifier(verifier):
        pass
    def got_versions(versions):
        pass
    def received(plaintext):
        pass
    def closed(result):
        pass

class IBoss(Interface):
    pass
class INameplate(Interface):
    pass
class IMailbox(Interface):
    pass
class ISend(Interface):
    pass
class IOrder(Interface):
    pass
class IKey(Interface):
    pass
class IReceive(Interface):
    pass
class IRendezvousConnector(Interface):
    pass
class ILister(Interface):
    pass
class ICode(Interface):
    pass
class IInput(Interface):
    pass
class IAllocator(Interface):
    pass
class ITerminator(Interface):
    pass

class ITiming(Interface):
    pass
class ITorManager(Interface):
    pass
class IWordlist(Interface):
    def choose_words(length):
        """Randomly select LENGTH words, join them with hyphens, return the
        result."""
    def get_completions(prefix):
        """Return a list of all suffixes that could complete the given
        prefix."""

# These interfaces are public, and are re-exported by __init__.py

class IDeferredWormhole(Interface):
    def get_welcome():
        """
        Wait for the 'welcome message' dictionary, sent by the server upon
        first connection.

        :rtype: ``Deferred[dict]``
        :return: the welcome dictionary, when it arrives from the server
        """

    def allocate_code(code_length=2):
        """
        Ask the wormhole to allocate a nameplate and generate a random code.

        When the code is ready, any Deferreds returned by ``get_code()`` will
        be fired.  Only one of generate_code/set_code/input_code may be used.

        :param int code_length: the number of random words to use.  More
            words means the code is harder to guess.  Defaults to 2.

        :return: None

            ~mod.class
        """

    def set_code(code):
        """
        Tell the wormhole to use a specific code, either copied from a
        wormhole that used ``allocate_code``, or created out-of-band by
        humans (and given to ``set_code`` on both wormholes).

        Any Deferreds returned by ``get_code()`` will be fired as soon as
        this is called.  Only one of generate_code/set_code/input_code may be
        used.

        :return: None
        """

    def input_code():
        """
        Ask the wormhole to perform interactive entry of the code, with
        completion on the nameplate and/or words.

        This does not actually interact with the user, but instead returns a
        'code-entry helper' object.  The application is responsible for doing
        the IO: the helper is used to get completion lists and to submit the
        finished code.  See ``input_with_completion`` for a wrapper function
        that uses ``readline`` to do CLI-style input completion.

        Any Deferreds returned by ``get_code()`` will be fired when the final
        code is submitted to the helper.  Only one of
        generate_code/set_code/input_code may be used.

        :return: a code-entry helper instance
        :rtype: IHelper
        """

    def get_code():
        """
        Wait for the wormhole code to be established, then return the code.
        This is really only useful on the initiating side, which needs to
        deliver the code to the user (so the user can dictate it to the other
        user, who can deliver it to their application with ``set_code`` or
        ``input_code``).  On the receiving side, merely submitting the code
        is sufficient.

        The wormhole code is always unicode (so ``str`` on py3, ``unicode``
        on py2).

        For ``allocate_code``, this must wait for the server to allocate a
        nameplate.  For ``input_code``, it waits for the final code to be
        submitted to the helper.  For ``set_code``, it fires immediately.

        :return: the wormhole code
        :rtype: ``Deferred[str]``
        """

    def get_unverified_key():
        """
        Wait for key-exchange to occur, then return the raw unverified SPAKE2
        key.  When this fires, we have not seen any evidence that anyone else
        shares this key (nor have we seen evidence of a failed attack: e.g. a
        payload encrypted with a different key).

        This is only useful for testing, and for noticing a significant delay
        between the key-agreement message and the subsequent key-verification
        ("versions") message.

        :return: the raw unverified SPAKE2 key
        :rtype: ``Deferred[bytes]``
        """

    def get_verifier():
        """
        Wait for key verification to occur, then return the verifier string.
        When this fires, we have seen at least one validly-encrypted message
        from our peer, indicating that we have established a shared secret
        key with some party who knows (or correctly guessed) the wormhole
        code.

        The verifier string (bytes) can be displayed to the user (perhaps as
        hex), who can manually compare it with the peer's verifier, to obtain
        more confidence in the secrecy of the established key.

        If we receive an invalid encrypted message (such as what would happen
        if an attacker tried and failed to guess the wormhole code), this
        will instead errback with a ``WrongPasswordError``.

        :return: the verifier string, after a valid encrypted message has
                 arrived
        :rtype: ``Deferred[bytes]``
        """

    def get_versions():
        """
        Wait for a valid VERSION message to arrive, then return the peer's
        "versions" dictionary.  This dictionary comes from the ``versions=``
        argument to the peer's ``wormhole()`` constructor, and is meant to
        assist with capability-negotiation between the two peers.  In
        particular, the ``versions`` dictionary is delivered before either
        side has called ``send_message()``, so it can influence the first
        message sent to a peer that is too old to use that first message for
        negotiation purposes.

        If we receive any invalid encrypted message (such as what would
        happen if an attacker tried and failed to guess the wormhole code),
        this will instead errback with a ``WrongPasswordError``.

        :return: the verisions dictionary
        :rtype: ``Deferred[dict]``
        """

    def derive_key(purpose, length):
        """
        Derive a purpose-specific key.

        This combines the master SPAKE2 key with the given purpose string and
        deterministically derives a new key of the requested length.  Any two
        connected Wormhole objects which call ``derive_key`` with the same
        purpose and length will get the same key.  This can be used to
        encrypt or sign other messages, or exchanged for verification
        purposes.  The master key will remain secret even if you reveal a
        derivative key.

        This must be called after the key has been established, so after any
        of
        ``get_unverified_key()/get_verifier()/get_versions()/get_message()``
        have fired.  ``derive_key()`` returns immediately, rather than
        returning a ``Deferred``.

        :return: a derivative key, of the requested length
        :rtype: ``bytes``
        """

    def send_message(msg):
        """
        Send a message to the connected peer.

        This accepts a bytestring, and queues it for encryption and delivery
        to the other side, where it will eventually appear in
        ``get_message()``.  Messages are delivered in-order, and complete
        (the Wormhole is a record-pipe, not a byte-pipe).

        This can be called at any time, even before setting the wormhole
        code.  The message will be queued for delivery after the master key
        is established.

        :return: None
        """

    def get_message():
        """
        Wait for, and return, the next message.

        This returns a Deferred that will fire when the next (sequential)
        application message has been received and successfully decrypted.
        Messages will be delivered in-order and intact (the Wormhole is a
        record-pipe, not a byte-pipe).

        This can be called at any time, even before setting the wormhole
        code.  The Deferred will not fire until key-negotiation has completed
        and a validly-encrypted message has arrived.

        If we receive any invalid encrypted message (such as what would
        happen if an attacker tried and failed to guess the wormhole code),
        this will instead errback with a ``WrongPasswordError``.

        :return: the next decrypted message
        :rtype: ``Deferred[bytes]``
        """

    def close():
        """
        Close the wormhole.

        This frees all resources associated with the wormhole (including
        server-side queues and any established network connections).

        For operational purposes, it informs the server that the wormhole
        closed "happy".  Less-happy moods may be reported if the connection
        closed due to a ``WrongPasswordError`` or because of a timeout.

        ``close()`` returns a Deferred, which fires after the server has been
        informed and the sockets have been shut down.  One-shot applications
        should delay shutdown until this Deferred has fired, to increase the
        chances that server resources will be freed.  Long-running
        applications can probably ignore the Deferred, as they'll probably
        remain running long enough to allow the shutdown to complete.

        The Deferred will errback if the wormhole had problems, like a
        ``WrongPasswordError``.

        :return: a Deferred that fires when shutdown is complete
        :rtype: ``Deferred``
        """


class IJournal(Interface): # TODO: this needs to be public
    pass
