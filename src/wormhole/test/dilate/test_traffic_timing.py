import pytest
from ..._dilation.manager import TrafficTimer

# Correct operation of the TrafficTiming state-machine

class TimerFixture:
    def __init__(self):
        self.reconnects = []
        self.timers = []

        self.timer_count = 0
        self.reconnect_count = 0
        self.t = TrafficTimer(self.do_reconnect, self.start_timer)

    def start_timer(self):
        self.timers.append(self.timer_count)
        self.timer_count += 1

    def do_reconnect(self):
        self.reconnects.append(self.reconnect_count)
        self.reconnect_count += 1


@pytest.fixture()
def traffic_timer():
    yield TimerFixture()


def test_start(traffic_timer):
    """
    We immedialte start a timer after connecting
    """
    traffic_timer.t.got_connection()
    assert traffic_timer.timers == [0]


def test_one_interval(traffic_timer):
    """
    No reconnect when only a single interval has passed
    """
    traffic_timer.t.got_connection()
    traffic_timer.t.interval_elapsed()
    # timer must be re-started after one elapsed interval
    assert traffic_timer.timers == [0, 1]
    assert traffic_timer.reconnects == []

def test_expired_connection(traffic_timer):
    """
    Trigger a reconnect after two intervals pass with no traffic seen
    """
    traffic_timer.t.got_connection()
    traffic_timer.t.interval_elapsed()
    traffic_timer.t.interval_elapsed()
    # timer _not_ re-started after second interval, because now
    # we're re-connecting
    assert traffic_timer.timers == [0, 1]
    assert traffic_timer.reconnects == [0]

    # at some point after the re-connect trigger we would lose our
    # connection
    traffic_timer.t.lost_connection()
    assert traffic_timer.timers == [0, 1]
    assert traffic_timer.reconnects == [0]
