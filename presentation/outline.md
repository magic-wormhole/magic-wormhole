# Introduction (10min)

- introduction to the magic-wormhole tool: what it does, how to use it, demo
  (5min)
- comparison to other file-transfer techniques: ease-of-use (what data the
  humans are responsible for transferring, in which directions), security
  (which eavesdroppers get to see the file) (3min)
- motivation: why privacy and security matter (2min)

# Architecture (9min)
- network architecture: Rendezvous Server, Relay Server, direct connections,
  relayed connections (3min)
- PAKE (Password-Authenticated Key Exchange): what it does, a little bit
  about how it works (3min)
- security architecture: locally-generated "invitation code",
  server-mediated PAKE conversation, encrypted exchange of IP addresses and
  transit information, finally encrypted file transfer (3min)

# Beyond File Transfer (7min)

- describe library API (3min)
- describe other applications that could use this technique (2min)
- summarize future work: improved transit techniques (STUN, WebRTC, P2P
  things), JS/web versions, implement in other languages (2min)

# Q+A (4min)
