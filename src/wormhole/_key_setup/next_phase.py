
def next_phase(old):
    if old == "pake":
        return "pake-1"
    if old.startswith("pake-"):
        n = int(old[len("pake-"):])
        assert isinstance(int, n)
        return "pake-%d" % (n+1)
    raise ValueError("unknown old phase '%s'" % old)
