import sys, os, io
from twisted.internet import reactor, protocol, task, defer
from twisted.python.procutils import which
from twisted.python import usage

# run the command with python's deprecation warnings turned on, capturing
# stderr. When done, scan stderr for warnings, write them to a separate
# logfile (so the buildbot can see them), and return rc=1 if there were any.

class Options(usage.Options):
    optParameters = [
        ["warnings", None, None, "file to write warnings into at end of test run"],
        ]

    def parseArgs(self, command, *args):
        self["command"] = command
        self["args"] = list(args)

    description = """Run as:
PYTHONWARNINGS=default::DeprecationWarning python run-deprecations.py [--warnings=STDERRFILE]  COMMAND ARGS..
"""

class RunPP(protocol.ProcessProtocol):
    def outReceived(self, data):
        self.stdout.write(data)
        sys.stdout.write(data)
    def errReceived(self, data):
        self.stderr.write(data)
        sys.stderr.write(data)
    def processEnded(self, reason):
        signal = reason.value.signal
        rc = reason.value.exitCode
        self.d.callback((signal, rc))

@defer.inlineCallbacks
def run_command(main):
    config = Options()
    config.parseOptions()

    command = config["command"]
    if "/" in command:
        # don't search
        exe = command
    else:
        executables = which(command)
        if not executables:
            raise ValueError("unable to find '%s' in PATH (%s)" %
                             (command, os.environ.get("PATH")))
        exe = executables[0]

    pw = os.environ.get("PYTHONWARNINGS")
    DDW = "default::DeprecationWarning"
    if pw != DDW:
        print "note: $PYTHONWARNINGS is '%s', not the expected %s" % (pw, DDW)
        sys.stdout.flush()

    pp = RunPP()
    pp.d = defer.Deferred()
    pp.stdout = io.BytesIO()
    pp.stderr = io.BytesIO()
    reactor.spawnProcess(pp, exe, [exe] + config["args"], env=None)
    (signal, rc) = yield pp.d

    # maintain ordering, but ignore duplicates (for some reason, either the
    # 'warnings' module or twisted.python.deprecate isn't quashing them)
    already = set()
    warnings = []
    def add(line):
        if line in already:
            return
        already.add(line)
        warnings.append(line)

    pp.stdout.seek(0)
    for line in pp.stdout.readlines():
        if "DeprecationWarning" in line:
            add(line) # includes newline

    pp.stderr.seek(0)
    for line in pp.stderr.readlines():
        if "DeprecationWarning" in line:
            add(line)

    if warnings:
        if config["warnings"]:
            with open(config["warnings"], "wb") as f:
                print >>f, "".join(warnings)
        print "ERROR: %d deprecation warnings found" % len(warnings)
        sys.exit(1)

    print "no deprecation warnings"
    if signal:
        sys.exit(signal)
    sys.exit(rc)


task.react(run_command)
