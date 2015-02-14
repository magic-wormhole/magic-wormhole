import os
from .wordlist import byte_to_even_word, byte_to_odd_word

def make_code(channel_id):
    even_word = byte_to_even_word[os.urandom(1)]
    odd_word = byte_to_odd_word[os.urandom(1)]
    return "%d-%s-%s" % (channel_id, even_word, odd_word)

def extract_channel_id(code):
    channel_id = int(code.split("-")[0])
    return channel_id
