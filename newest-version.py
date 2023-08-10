#
# this updates the (tagged) version of the software
#
# it will only update the "minor" version (e.g. 0.12.* -> 0.13.0)
#
# Any "options" are hard-coded in here (e.g. the GnuPG key to use)
#

author = "meejah <meejah@meejah.ca>"


import sys
import time
import itertools
from datetime import datetime

from dulwich.repo import Repo
from dulwich.porcelain import (
    tag_list,
    tag_create,
    status,
)

from twisted.internet.task import (
    react,
)
from twisted.internet.defer import (
    ensureDeferred,
)


def existing_tags(git):
    versions = [
        tuple(map(int, v.decode("utf8").split(".")))
        for v in tag_list(git)
    ]
    return versions


async def main(reactor):
    git = Repo(".")
    print("{}.{}.{}".format(*sorted(existing_tags(git))[-1]))


if __name__ == "__main__":
    react(lambda r: ensureDeferred(main(r)))
