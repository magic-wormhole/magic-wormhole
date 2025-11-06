class _Role:
    def __init__(self, which):
        self._which = which

    def __repr__(self):
        return f"Role({self._which})"


LEADER, FOLLOWER = _Role("LEADER"), _Role("FOLLOWER")
