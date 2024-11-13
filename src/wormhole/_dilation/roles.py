class _Role(object):
    def __init__(self, which):
        self._which = which

    def __repr__(self):
        return "Role(%s)" % self._which


LEADER, FOLLOWER = _Role("LEADER"), _Role("FOLLOWER")
