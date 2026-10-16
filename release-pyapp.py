#
# this helps someone with permissions and a proper token to:
#
# - download PyApp "artifacts" from a particular build
# - (e.g. the one where you merge the "prepare-0.24.0" branch to master)
# - un-package the zip files
# - create a "github release" (using token)
# - create a "release artifact" for each thing from the zip files

from twisted.internet.task import react
from twisted.internet.defer import ensureDeferred
from twisted.web.http_headers import Headers
import treq
import json
from io import BytesIO
from zipfile import ZipFile


async def main(reactor):
    with open("PRIVATE-github-releases-token", "r") as f:
        token = f.read().strip()

    headers = Headers({
        "User-Agent": ["release-flow"],
        "Accept": ["application/vnd.github+json"],
        "Authorization": ["Bearer {}".format(token)],
        "X-GitHub-Api-Version": ["2026-03-10"],
    })

    # get ... all artifacts?
    resp = await treq.get(
        "https://api.github.com/repos/magic-wormhole/magic-wormhole/actions/artifacts",
        headers=headers,
    )
    body = await resp.content()
    js = json.loads(body)

    if False:
        for artifact in js['artifacts']:
            print(artifact)
        return


    urls = [
        (artifact['name'], artifact['archive_download_url'])
        for artifact in js['artifacts']
    ]

    for name, url in urls:
        print(name, url)
        zipdata = BytesIO()
        resp = await treq.get(url, headers=headers)

        print("   ", end="", flush=True)
        def foo(b):
            print(".", end="", flush=True)
            zipdata.write(b)
        await resp.collect(foo)
        print("done")

        zf = ZipFile(zipdata)
        if name == "wormhole-windows-latest":
            with zf.open("pyapp.exe") as f:
                with open("magic-wormhole.exe", "wb") as out:
                    out.write(f.read())
        elif name == "wormhole-macos-latest":
            with zf.open("pyapp") as f:
                with open("MagicWormhole", "wb") as out:
                    out.write(f.read())
        elif name == "wormhole-ubuntu-22.04":
            with zf.open("pyapp") as f:
                with open("wormhole", "wb") as out:
                    out.write(f.read())



"""
curl -L \
  -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer <YOUR-TOKEN>" \
  -H "X-GitHub-Api-Version: 2026-03-10" \
  https://api.github.com/repos/OWNER/REPO/releases \
  -d '{"tag_name":"v1.0.0","target_commitish":"master","name":"v1.0.0","body":"Description of the release","draft":false,"prerelease":false,"generate_release_notes":false}'
"""


if __name__ == "__main__":
    react(lambda r: ensureDeferred(main(r)))
