from __future__ import print_function
import sys, os, subprocess

if sys.argv[1] == "scan":
    src_root = os.path.join(*sys.argv[2].split("/"))
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
    sources = find_all(abs_src_root)

    sources.sort()
    for s in sources:
        assert s.startswith(abs_src_root+"/")
        print(s[len(abs_src_root+"/"):])

    sys.exit(0)

SOURCES = """\
__init__.py
_allocator.py
_boss.py
_code.py
_input.py
_interfaces.py
_key.py
_lister.py
_mailbox.py
_nameplate.py
_order.py
_receive.py
_rendezvous.py
_rlcompleter.py
_send.py
_terminator.py
_version.py
_wordlist.py
cli/__init__.py
cli/cli.py
cli/cmd_receive.py
cli/cmd_send.py
cli/cmd_ssh.py
cli/public_relay.py
cli/welcome.py
errors.py
ipaddrs.py
journal.py
server/__init__.py
server/cli.py
server/cmd_server.py
server/cmd_usage.py
server/database.py
server/rendezvous.py
server/rendezvous_websocket.py
server/server.py
server/transit_server.py
timing.py
tor_manager.py
transit.py
util.py
wormhole.py
xfer_util.py
"""

#print("scanning %d source files" % len(sources))
#mypy = os.path.join("mypy-venv", "bin", "mypy")
#extra_args = ["--silent-imports"]
#rc = subprocess.call([mypy] + extra_args + sources)
#sys.exit(rc)

