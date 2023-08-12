#
# this updates the (tagged) version of the software
#
# it will only update the "minor" version (e.g. 0.12.* -> 0.13.0)
#
# Any "options" are hard-coded in here (e.g. the GnuPG key to use)
#

import sys
import time
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

author = "meejah <meejah@meejah.ca>"


def existing_tags(git):
    versions = [
        tuple(map(int, v.decode("utf8").split(".")))
        for v in tag_list(git)
    ]
    return versions


def create_new_version(git, only_patch):
    versions = existing_tags(git)
    major, minor, patch = sorted(versions)[-1]
    if only_patch:
        next_version = "{}.{}.{}".format(major, minor, patch + 1)
    else:
        next_version = "{}.{}.{}".format(major, minor + 1, 0)
    return next_version


async def main(reactor):
    git = Repo(".")

    # including untracked files can be very slow (if there are lots,
    # like in virtualenvs) and we don't care anyway
    st = status(git, untracked_files="no")
    if any(st.staged.values()) or st.unstaged:
        print("unclean checkout; aborting")
        raise SystemExit(1)

    for arg in sys.argv[1:]:
        if arg not in ("--no-tag", "--patch"):
            print("unknown arg: {}".format(arg))
            raise SystemExit(2)

    v = create_new_version(git, "--patch" in sys.argv)
    if "--no-tag" in sys.argv:
        print(v)
        return

    print("Latest version: {}.{}.{}".format(*sorted(existing_tags(git))[-1]))
    print("New tag will be {}".format(v))

    # the "tag time" is seconds from the epoch .. we quantize these to
    # the start of the day in question, in UTC.
    now = datetime.now()
    s = now.utctimetuple()
    ts = int(
        time.mktime(
            time.struct_time((
                s.tm_year, s.tm_mon, s.tm_mday, 0, 0, 0, 0, s.tm_yday, 0
            ))
        )
    )
    tag_create(
        repo=git,
        tag=v.encode("utf8"),
        author=author.encode("utf8"),
        message="release magic-wormhole-{}".format(v).encode("utf8"),
        annotated=True,
        objectish=b"HEAD",
        sign=author.encode("utf8"),
        tag_time=ts,
        tag_timezone=0,
    )

    print("Tag created locally, it is not pushed")
    print("To push it run something like:")
    print("   git push origin {}".format(v))


if __name__ == "__main__":
    react(lambda r: ensureDeferred(main(r)))
