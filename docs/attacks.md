# Known Vulnerabilities

## Low-probability Man-In-The-Middle Attacks

By default, wormhole codes contain 16 bits of entropy. If an attacker
can intercept your network connection (either by owning your network, or
owning the rendezvous server), they can attempt an attack. They will
have a one-in-65536 chance of successfully guessing your code, allowing
them to pose as your intended partner. If they succeed, they can turn
around and immediately start a new wormhole (using the same code),
allowing your partner to connect to them instead of you. By passing,
observing, and possibly modifying messages between these two
connections, they could perform an MitM (Man In The Middle) attack.

If the server refused to re-use the same channel id (aka "nameplate")
right away (issue #31), a network attacker would be unable to set up the
second connection, cutting this attack in half. An attacker who controls
the server would not be affected.

Basic probability tells us that peers will see a large number of
WrongPasswordErrors before the attacker has a useful chance of
successfully guessing any wormhole code. You should expect to see about
32000 failures before they have a 50% chance of being successful. If you
see many failures, and think someone is trying to guess your codes, you
can use e.g. `wormhole send --code-length=4` to make a longer code
(reducing their chances significantly).

Of course, an attacker who learns your secret wormhole code directly
(because you delivered it over an insecure channel) can perform this
attack with 100% reliability.


## DoS Attack on the Rendezvous Server

Wormhole codes can be so short because they implicitly contain a common
rendezvous server URL (any two applications that use magic-wormhole
should be configured to use the same server). As a result, successful
operation depends upon both clients being able to contact that server,
making it a SPOF (single point of failure).

In particular, grumpy people could disrupt service for everyone by
writing a program that just keeps connecting to the rendezvous server,
pretending to be real clients, and claiming messages meant for
legitimate users.

I do not have any good mitigations for this attack, and functionality
may depend upon the continued goodwill of potential vandals. The weak
ones that I've considered (but haven't implemented yet) include:

* hashcash challenges when the server is under attack
* per-IP rate-limiting (although I'd want to be careful about protecting
  the privacy of the IP addresses, so it'd need a rotating hash seed)
* require users to go through some external service (maybe ReCAPTCHA?)
  and get a rate-limiting ticket before claiming a channel
* shipping an attack tool (flooding the first million channels), as part
  of the distribution, in a subcommand named `wormhole
  break-this-useful-service-for-everybody-because-i-am-a-horrible-person`,
  in the hopes that pointing out how easy it is might dissuade a few
  would-be vandals from feeling a sense of accomplishment at writing
  their own :). Not sure it would help much, but I vaguely remember
  hearing about something similar in the early multi-user unix systems
  (a publically-executable /bin/crash or something, which new users
  tended to only run once before learning some responsibility).

Using the secret words as part of the "channel id" isn't safe, since it
would allow a network attacker, or the rendezvous server, to deduce what
the secret words are: since they only have 16 bits of entropy, the
attacker just makes a table of hash(words) -> channel-id, then reverses
it. To make that safer we'd need to increase the codes to maybe 80 bits
(ten words), plus do some significant key-stretching (like 5-10 seconds
of scrypt or argon2), which would increase latency and CPU demands, and
still be less secure overall.

The core problem is that, because things are so easy for the legitimate
participants, they're really easy for the attacker too. Short wormhole
codes are the easiest to use, but they make it for a trivially
predictable channel-id target.

I don't have a good answer for this one. I'm hoping that it isn't
sufficiently interesting to attack that it'll be an issue, but I can't
think of any simple answers. If the API is sufficiently compelling for
other applications to incorporate Wormhole "technology" into their apps,
I'm expecting that they'll run their own rendezvous server, and of
course those apps can incorporate whatever sort of DoS protection seems
appropriate. For the built-in/upstream send-text/file/directory tools,
using the public relay that I run, it may just have to be a best-effort
service, and if someone decides to kill it, it fails.

See #107 for more discussion.
