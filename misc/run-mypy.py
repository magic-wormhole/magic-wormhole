from __future__ import print_function
import sys, os, subprocess

src_root = os.path.join(*sys.argv[1].split("/"))
abs_src_root = os.path.abspath(src_root)

def find_all(abs_src_root):
    rootlen = len(abs_src_root+os.pathsep)
    # find all our own source files, excluding tests
    sources = []
    for (path, dirs, files) in os.walk(abs_src_root):
        relpath = os.path.join(abs_src_root, path)[rootlen:]
        if relpath.split(os.pathsep)[0] == "test":
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            sources.append(os.path.join(abs_src_root, path, fn))
    return sources
#sources = find_all(abs_src_root)

SOURCES = """\
__init__.py
_version.py
channel_monitor.py
cli/__init__.py
cli/cli.py
cli/cmd_receive.py
cli/cmd_send.py
cli/cmd_ssh.py
cli/public_relay.py
codes.py
errors.py
ipaddrs.py
server/__init__.py
server/cli.py
server/cmd_server.py
server/cmd_usage.py
server/database.py
server/rendezvous.py
server/rendezvous_websocket.py
server/runner.py
server/server.py
server/transit_server.py
timing.py
tor_manager.py
transit.py
util.py
wordlist.py
wormhole.py
xfer_util.py
"""

sources.sort()
for s in sources:
    assert s.startswith(abs_src_root+"/")
    print(s[len(abs_src_root+"/"):])

#print("scanning %d source files" % len(sources))
#mypy = os.path.join("mypy-venv", "bin", "mypy")
#extra_args = ["--silent-imports"]
#rc = subprocess.call([mypy] + extra_args + sources)
#sys.exit(rc)
