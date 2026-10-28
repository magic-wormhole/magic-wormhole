from spake2 import SPAKE2_Symmetric
from ..util import to_bytes

class SPAKE2_Helper:
    def __init__(self, appid: str):
        self._appid = appid
        self._code = None
        self._sp = None
        self._msg1 = None
        self._finished = False
    def start(self, code: str):
        if not self._code:
            self._code = code
        assert code == self._code
        if not self._msg1:
            code_b = to_bytes(code)
            appid_b = to_bytes(self._appid)
            self._sp = SPAKE2_Symmetric(code_b, idSymmetric=appid_b)
            self._msg1 = self._sp.start()
        return self._msg1
    def finish(self, msg2):
        assert not self._finished
        self._finished
        return self._sp.finish(msg2)
