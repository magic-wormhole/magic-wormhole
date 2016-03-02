from __future__ import print_function
import json, time

class DebugTiming:
    def __init__(self):
        self.data = []
    def add_event(self, name, **details):
        # [ start, [stop], name, start_details{}, stop_details{} ]
        self.data.append( [time.time(), None, name, details, {}] )
        return len(self.data)-1
    def finish_event(self, index, **details):
        self.data[index][1] = time.time()
        self.data[index][4] = details
    def write(self, fn, stderr):
        with open(fn, "wb") as f:
            json.dump(self.data, f)
            f.write("\n")
        print("Timing data written to %s" % fn, file=stderr)
