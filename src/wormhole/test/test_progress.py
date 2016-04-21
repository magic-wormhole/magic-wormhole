from __future__ import print_function
import io, time
from twisted.trial import unittest
from ..cli import progress

class Progress(unittest.TestCase):
    def test_time(self):
        p = progress.ProgressPrinter(1e6, None)
        start = time.time()
        now = p._now()
        finish = time.time()
        self.assertTrue(start <= now <= finish, (start, now, finish))

    def test_basic(self):
        stdout = io.StringIO()
        p = progress.ProgressPrinter(1e6, stdout)
        p._now = lambda: 0.0
        p.start()

        erase = u"\r"+u" "*70
        expected = erase
        fmt = "Progress: %-40s %3d%%  %4d%s"
        expected += u"\r" + fmt % ("", 0, 0, "KB")
        self.assertEqual(stdout.getvalue(), expected)

        p.update(1e3) # no change, too soon
        self.assertEqual(stdout.getvalue(), expected)

        p._now = lambda: 1.0
        p.update(1e3) # enough "time" has passed
        expected += erase + u"\r" + fmt % ("", 0, 1, "KB")
        self.assertEqual(stdout.getvalue(), expected)

        p._now = lambda: 2.0
        p.update(500e3)
        expected += erase + u"\r" + fmt % ("#"*20, 50, 500, "KB")
        self.assertEqual(stdout.getvalue(), expected)

        p._now = lambda: 3.0
        p.finish()
        expected += erase + u"\r" + fmt % ("#"*40, 100, 1000, "KB")
        expected += u"\n"
        self.assertEqual(stdout.getvalue(), expected)

    def test_units(self):
        def _try(size):
            stdout = io.StringIO()
            p = progress.ProgressPrinter(size, stdout)
            p.finish()
            return stdout.getvalue()

        fmt = "Progress: %-40s %3d%%  %4d%s"
        def _expect(count, units):
            erase = u"\r"+u" "*70
            expected = erase
            expected += u"\r" + fmt % ("#"*40, 100, count, units)
            expected += u"\n"
            return expected

        self.assertEqual(_try(900), _expect(900, "B"))
        self.assertEqual(_try(9e3), _expect(9000, "B"))
        self.assertEqual(_try(90e3), _expect(90, "KB"))
        self.assertEqual(_try(900e3), _expect(900, "KB"))
        self.assertEqual(_try(9e6), _expect(9000, "KB"))
        self.assertEqual(_try(90e6), _expect(90, "MB"))
        self.assertEqual(_try(900e6), _expect(900, "MB"))
        self.assertEqual(_try(9e9), _expect(9000, "MB"))
        self.assertEqual(_try(90e9), _expect(90, "GB"))
        self.assertEqual(_try(900e9), _expect(900, "GB"))
