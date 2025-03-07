

from twisted.trial import unittest


from ..._dilation.manager import TrafficTimer


class TimingState(unittest.TestCase):
    """
    Correct operation of the TrafficTiming state-machine
    """

    def setUp(self):
        self.reconnects = []
        self.timers = []
        self._timer_count = 0

        def start_timer():
            self.timers.append(self._timer_count)
            self._timer_count += 1
        self.t = TrafficTimer(self.reconnects.append, start_timer)

    def test_happy(self):
        self.t.got_connection()
        self.assertEqual(self.timers, [0])
        self.t.interval_elapsed()
        self.assertEqual(self.timers, [0, 1])
        self.assertEqual(self.reconnects, [])
