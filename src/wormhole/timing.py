from __future__ import print_function
import json, time

class DebugTiming:
    def __init__(self):
        self.data = []
    def add_event(self, name, when=None, **details):
        # [ start, [server_sent], [stop], name, start_details{}, stop_details{} ]
        if when is None:
            when = time.time()
        when = float(when)
        self.data.append( [when, None, None, name, details, {}] )
        return len(self.data)-1
    def finish_event(self, index, server_sent=None, **details):
        if server_sent is not None:
            self.data[index][1] = float(server_sent)
        self.data[index][2] = time.time()
        self.data[index][5] = details
    def write(self, fn, stderr):
        with open(fn, "wb") as f:
            json.dump(self.data, f)
            f.write("\n")
        print("Timing data written to %s" % fn, file=stderr)
