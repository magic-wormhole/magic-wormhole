
def next_phase(old):
    if old == "pake":
        return "pake-1"
    if old.startswith("pake-"):
        n = int(old[len("pake-"):])
        assert isinstance(n, int)
        return "pake-%d" % (n+1)
    raise ValueError("unknown old phase '%s'" % old)
