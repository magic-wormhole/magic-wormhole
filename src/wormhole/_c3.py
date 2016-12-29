from ._machine import Machine

class WormholeMachine:
    m = Machine()

    know_nothing = m.State("know_nothing", initial=True)
    know_code = m.State("know_code")
    know_key = m.State("know_key", color="orange")
    #verified_key = m.State("verified_key", color="green")
    closed = m.State("closed")

    API_send = m.Event("API_send")
    WM_set_code = m.Event("WM_set_code")
    WM_rx_pake = m.Event("WM_rx_pake")
    #WM_rx_msg = m.Event("WM_rx_msg")
    close = m.Event("close")

    @m.action()
    def set_code(self):
        self._MM.set_nameplate()
        self._build_pake()
        self._MM.send(self._pake)
    @m.action()
    @m.outcome("pake ok")
    @m.outcome("pake bad")
    def compute_key(self):
        self._key = self._computer_stuff()
        if 1:
            return "pake ok"
        else:
            return "pake bad"
    @m.action()
    def send_version(self):
        self._MM.send(self._version)
    @m.action()
    @m.outcome("verify ok")
    @m.outcome("verify bad")
    def verify(self, msg, verify_ok, verify_bad):
        try:
            decrypted = decrypt(self._key, msg)
            return verify_ok(decrypted)
        except CryptoError:
            return verify_bad()
    @m.action()
    def queue1(self, msg):
        self._queue.append(msg)
    @m.action()
    def queue2(self, msg):
        self._queue.append(msg)
    @m.action()
    def close_lonely(self):
        self._MM.close("lonely")
    @m.action()
    def close_scary(self):
        self._MM.close("scary")

    compute_key.upon("pake ok", goto=send_version)
    compute_key.upon("pake bad", goto=close_scary)
    know_nothing.upon(API_send, goto=queue1)
    queue1.goto(know_nothing)
    know_nothing.upon(WM_set_code, goto=set_code)
    set_code.goto(know_code)
    know_code.upon(API_send, goto=queue2)
    queue2.goto(know_code)
    know_code.upon(WM_rx_pake, goto=compute_key)
    compute_key.goto(send_version)
    send_version.goto(know_key)
    know_code.upon(close, goto=close_lonely)
    know_key.upon(close, goto=close_lonely)
    close_lonely.goto(closed)


if __name__ == "__main__":
    import sys
    WM = WormholeMachine()
    WM.m._dump_dot(sys.stdout)
