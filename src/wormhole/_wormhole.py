
class Wormhole:
    m = MethodicalMachine()

    def __init__(self, ws_url, reactor):
        self._relay_client = WSRelayClient(self, ws_url, reactor)
        # This records all the messages we want the relay to have. Each time
        # we establish a connection, we'll send them all (and the relay
        # server will filter out duplicates). If we add any while a
        # connection is established, we'll send the new ones.
        self._outbound_messages = []

    # these methods are called from outside
    def start(self):
        self._relay_client.start()

    # and these are the state-machine transition functions, which don't take
    # args
    @m.state()
    def closed(initial=True): pass
    @m.state()
    def know_code_not_mailbox(): pass
    @m.state()
    def know_code_and_mailbox(): pass # no longer need nameplate
    @m.state()
    def waiting_first_msg(): pass # key is established, want any message
    @m.state()
    def processing_version(): pass
    @m.state()
    def processing_phase(): pass
    @m.state()
    def open(): pass # key is verified, can post app messages
    @m.state(terminal=True)
    def failed(): pass

    @m.input()
    def deliver_message(self, message): pass

    def w_set_seed(self, code, mailbox):
        """Call w_set_seed when we sprout a Wormhole Seed, which
        contains both the code and the mailbox"""
        self.w_set_code(code)
        self.w_set_mailbox(mailbox)

    @m.input()
    def w_set_code(self, code):
        """Call w_set_code when you learn the code, probably because the user
        typed it in."""
    @m.input()
    def w_set_mailbox(self, mailbox):
        """Call w_set_mailbox() when you learn the mailbox id, from the
        response to claim_nameplate"""
        pass


    @m.input()
    def rx_pake(self, pake): pass # reponse["message"][phase=pake]

    @m.input()
    def rx_version(self, version): # response["message"][phase=version]
        pass
    @m.input()
    def verify_good(self, verifier): pass
    @m.input()
    def verify_bad(self, f): pass

    @m.input()
    def rx_phase(self, message): pass
    @m.input()
    def phase_good(self, message): pass
    @m.input()
    def phase_bad(self, f): pass

    @m.output()
    def compute_and_post_pake(self, code):
        self._code = code
        self._pake = compute(code)
        self._post(pake=self._pake)
        self._ws_send_command("add", phase="pake", body=XXX(pake))
    @m.output()
    def set_mailbox(self, mailbox):
        self._mailbox = mailbox
    @m.output()
    def set_seed(self, code, mailbox):
        self._code = code
        self._mailbox = mailbox

    @m.output()
    def process_version(self, version): # response["message"][phase=version]
        their_verifier = com
        if OK:
            self.verify_good(verifier)
        else:
            self.verify_bad(f)
        pass

    @m.output()
    def notify_verified(self, verifier):
        for d in self._verify_waiters:
            d.callback(verifier)
    @m.output()
    def notify_failed(self, f):
        for d in self._verify_waiters:
            d.errback(f)

    @m.output()
    def process_phase(self, message): # response["message"][phase=version]
        their_verifier = com
        if OK:
            self.verify_good(verifier)
        else:
            self.verify_bad(f)
        pass

    @m.output()
    def post_inbound(self, message):
        pass

    @m.output()
    def deliver_message(self, message):
        self._qc.deliver_message(message)

    @m.output()
    def compute_key_and_post_version(self, pake):
        self._key = x
        self._verifier = x
        plaintext = dict_to_bytes(self._my_versions)
        phase = "version"
        data_key = self._derive_phase_key(self._side, phase)
        encrypted = self._encrypt_data(data_key, plaintext)
        self._msg_send(phase, encrypted)

    closed.upon(w_set_code, enter=know_code_not_mailbox,
                outputs=[compute_and_post_pake])
    know_code_not_mailbox.upon(w_set_mailbox, enter=know_code_and_mailbox,
                               outputs=[set_mailbox])
    know_code_and_mailbox.upon(rx_pake, enter=waiting_first_msg,
                               outputs=[compute_key_and_post_version])
    waiting_first_msg.upon(rx_version, enter=processing_version,
                           outputs=[process_version])
    processing_version.upon(verify_good, enter=open, outputs=[notify_verified])
    processing_version.upon(verify_bad, enter=failed, outputs=[notify_failed])
    open.upon(rx_phase, enter=processing_phase, outputs=[process_phase])
    processing_phase.upon(phase_good, enter=open, outputs=[post_inbound])
    processing_phase.upon(phase_bad, enter=failed, outputs=[notify_failed])
