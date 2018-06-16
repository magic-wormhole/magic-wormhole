from __future__ import absolute_import, print_function, unicode_literals


def handle_welcome(welcome, relay_url, my_version, stderr):
    if "motd" in welcome:
        motd_lines = welcome["motd"].splitlines()
        motd_formatted = "\n ".join(motd_lines)
        print(
            "Server (at %s) says:\n %s" % (relay_url, motd_formatted),
            file=stderr)

    # Only warn if we're running a release version (e.g. 0.0.6, not
    # 0.0.6+DISTANCE.gHASH). Only warn once.
    if (("current_cli_version" in welcome and
         "+" not in my_version and
         welcome["current_cli_version"] != my_version)):
        print(
            ("Warning: errors may occur unless both sides are running the"
             " same version"),
            file=stderr)
        print(
            "Server claims %s is current, but ours is %s" %
            (welcome["current_cli_version"], my_version),
            file=stderr)
