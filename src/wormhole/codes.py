import os, random

WORDLIST = ["able", "baker", "charlie"] # TODO: 1024

def make_code(channel_id):
    # TODO: confirm that random.choice() uses os.urandom properly and covers
    # the entire range with minimal bias. Many random.py functions do not,
    # but I think this one might. If not, build our own from os.urandom,
    # convert-to-int, and modulo.
    word = random.choice(WORDLIST)
    return "%d-%s" % (channel_id, word)

def extract_channel_id(code):
    channel_id = int(code.split("-")[0])
    return channel_id
