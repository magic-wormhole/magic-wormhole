from __future__ import print_function, absolute_import, unicode_literals
import json, time

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
        if exc_type:
            # inlineCallbacks uses a special exception (defer._DefGen_Return)
            # to deliver returnValue(), so if returnValue is used inside our
            # with: block, we'll mistakenly think it means something broke.
            # I've moved all returnValue() calls outside the 'with
            # timing.add()' blocks to avoid this, but if a new one
            # accidentally pops up, it'll get marked as an error. I used to
            # catch-and-release _DefGen_Return to avoid this, but removed it
            # because it requires referencing defer.py's private class
            self.finish(exception=str(exc_type))
        else:
            self.finish()

class DebugTiming:
    def __init__(self):
        self._events = []

    def add(self, name, when=None, **details):
        ev = Event(name, when, **details)
        self._events.append(ev)
        return ev

    def write(self, fn, stderr):
        with open(fn, "wt") as f:
            data = [ dict(name=e._name,
                          start=e._start, stop=e._stop,
                          details=e._details,
                          )
                     for e in self._events ]
            json.dump(data, f, indent=1)
            f.write("\n")
        print("Timing data written to %s" % fn, file=stderr)
