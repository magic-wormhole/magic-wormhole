from __future__ import print_function
import time

class ProgressPrinter:
    def __init__(self, expected, stdout, update_every=0.2):
        self._expected = expected
        self._stdout = stdout
        self._update_every = update_every

    def _now(self):
        return time.time()

    def start(self):
        self._print(0)
        self._next_update = self._now() + self._update_every

    def update(self, completed):
        now = self._now()
        if now < self._next_update:
            return
        self._next_update = now + self._update_every
        self._print(completed)

    def finish(self):
        self._print(self._expected)
        print(u"", file=self._stdout)

    def _print(self, completed):
        # scp does "<<FILENAME >>(13%  168MB  39.3MB/s   00:27 ETA)"
        # we do "Progress: ####         13%  168MB"
        fmt = "Progress: %-40s %3d%%  %4d%s"
        short_unit_size, short_unit_name = 1, "B"
        if self._expected > 9999:
            short_unit_size, short_unit_name = 1000, "KB"
        if self._expected > 9999*1000:
            short_unit_size, short_unit_name = 1000*1000, "MB"
        if self._expected > 9999*1000*1000:
            short_unit_size, short_unit_name = 1000*1000*1000, "GB"

        percentage_complete = ((1.0 * completed / self._expected)
                               if self._expected
                               else 1.0)
        bars = "#" * int(percentage_complete * 40)
        perc = int(100 * percentage_complete)
        short_unit_count = int(completed / short_unit_size)
        out = fmt % (bars, perc, short_unit_count, short_unit_name)
        print(u"\r"+" "*70, end=u"", file=self._stdout)
        print(u"\r"+out, end=u"", file=self._stdout)
        self._stdout.flush()
