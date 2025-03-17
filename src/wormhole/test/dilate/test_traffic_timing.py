

from twisted.trial import unittest


from ..._dilation.manager import TrafficTimer


class TimingState(unittest.TestCase):
    """
    Correct operation of the TrafficTiming state-machine
    """

    def setUp(self):
        self.reconnects = []
        self.timers = []

        timer_count = 0
        reconnect_count = 0

        def start_timer():
            nonlocal timer_count
            self.timers.append(timer_count)
            timer_count += 1

        def do_reconnect():
            nonlocal reconnect_count
            self.reconnects.append(reconnect_count)
            reconnect_count += 1
        self.t = TrafficTimer(do_reconnect, start_timer)

    def test_start(self):
        """
        We immedialte start a timer after connecting
        """
        self.t.got_connection()
        self.assertEqual(self.timers, [0])

    def test_one_interval(self):
        """
        No reconnect when only a single interval has passed
        """
        self.t.got_connection()
        self.t.interval_elapsed()
        # timer must be re-started after one elapsed interval
        self.assertEqual(self.timers, [0, 1])
        self.assertEqual(self.reconnects, [])

    def test_expired_connection(self):
        """
        Trigger a reconnect after two intervals pass with no traffic seen
        """
        self.t.got_connection()
        self.t.interval_elapsed()
        self.t.interval_elapsed()
        # timer _not_ re-started after second interval, because now
        # we're re-connecting
        self.assertEqual(self.timers, [0, 1])
        self.assertEqual(self.reconnects, [0])

        # at some point after the re-connect trigger we would lose our
        # connection
        self.t.lost_connection()
        self.assertEqual(self.timers, [0, 1])
        self.assertEqual(self.reconnects, [0])
