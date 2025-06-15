import json
import time

from zope.interface import implementer

from ._interfaces import ITiming


class Event:
    def __init__(self, name, when, **details):
        # data fields that will be dumped to JSON later
        self._name = name
        self._start = time.time() if when is None else float(when)
        self._stop = None
        self._details = details

    def detail(self, **details):
        self._details.update(details)

    def finish(self, when=None, **details):
        self._stop = time.time() if when is None else float(when)
        self.detail(**details)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.finish()


@implementer(ITiming)
class DebugTiming:
    def __init__(self):
        self._events = []

    def add(self, name, when=None, **details):
        ev = Event(name, when, **details)
        self._events.append(ev)
        return ev

    def write(self, fn, stderr):
        with open(fn, "wt") as f:
            data = [
                dict(
                    name=e._name,
                    start=e._start,
                    stop=e._stop,
                    details=e._details,
                ) for e in self._events
            ]
            json.dump(data, f, indent=1)
            f.write("\n")
        print(f"Timing data written to {fn}", file=stderr)
