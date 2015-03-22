import sys
from twisted.python import usage
from .. import public_relay

class SendTextOptions(usage.Options):
    def parseArgs(self, text):
        self["text"] = text
    synopsis = "TEXT"

class ReceiveTextOptions(usage.Options):
    def parseArgs(self, code=None):
        self["code"] = code
    synopsis = "[CODE]"

class SendFileOptions(usage.Options):
    def parseArgs(self, filename):
        self["filename"] = filename
    synopsis = "FILENAME"

class ReceiveFileOptions(usage.Options):
    optParameters = [
        ["output-file", "o", None, "File to create"],
        ]
    def parseArgs(self, code=None):
        self["code"] = code
    synopsis = "[CODE]"

class Options(usage.Options):
    synopsis = "\nUsage: wormhole <command>"
    optParameters = [
        ["relay-url", None, public_relay.RENDEZVOUS_RELAY,
         "rendezvous relay to use (URL)"],
        ["transit-helper", None, public_relay.TRANSIT_RELAY,
         "transit relay to use (tcp:HOST:PORT)"],
        ]
    subCommands = [("send-text", None, SendTextOptions, "Send a text message"),
                   ("send-file", None, SendFileOptions, "Send a file"),
                   ("receive-text", None, ReceiveTextOptions, "Receive a text message"),
                   ("receive-file", None, ReceiveFileOptions, "Receive a file"),
                   ]

    def getUsage(self, **kwargs):
        t = usage.Options.getUsage(self, **kwargs)
        return t + "\nPlease run 'wormhole <command> --help' for more details on each command.\n"

    def postOptions(self):
        if not hasattr(self, 'subOptions'):
            raise usage.UsageError("must specify a command")

def send_text(*args):
    from . import cmd_send_text
    return cmd_send_text.send_text(*args)

def receive_text(*args):
    from . import cmd_receive_text
    return cmd_receive_text.receive_text(*args)

def send_file(*args):
    from . import cmd_send_file
    return cmd_send_file.send_file(*args)

def receive_file(*args):
    from . import cmd_receive_file
    return cmd_receive_file.receive_file(*args)

DISPATCH = {"send-text": send_text,
            "receive-text": receive_text,
            "send-file": send_file,
            "receive-file": receive_file,
            }

def run(args, stdout, stderr, executable=None):
    """This is invoked directly by the 'wormhole' entry-point script. It can
    also invoked by entry() below."""
    config = Options()
    try:
        config.parseOptions(args)
    except usage.error, e:
        c = config
        while hasattr(c, 'subOptions'):
            c = c.subOptions
        print >>stderr, str(c)
        print >>stderr, e.args[0]
        return 1
    command = config.subCommand
    so = config.subOptions
    so["executable"] = executable
    try:
        #rc = DISPATCH[command](so, stdout, stderr)
        rc = DISPATCH[command](so)
        return rc
    except ImportError, e:
        print >>stderr, "--- ImportError ---"
        print >>stderr, e
        print >>stderr, "Please run 'python setup.py build'"
        raise
        return 1

def entry():
    """This is used by a setuptools entry_point. When invoked this way,
    setuptools has already put the installed package on sys.path ."""
    return run(sys.argv[1:], sys.stdout, sys.stderr, executable=sys.argv[0])
