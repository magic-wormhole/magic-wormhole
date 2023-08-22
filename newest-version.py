#
# print out the most-recent version
#

from dulwich.repo import Repo
from dulwich.porcelain import tag_list

from twisted.internet.task import react
from twisted.internet.defer import ensureDeferred


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
