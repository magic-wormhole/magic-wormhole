from __future__ import print_function
import os
from .wordlist import (byte_to_even_word, byte_to_odd_word,
                       even_words_lowercase, odd_words_lowercase)

def make_code(channel_id):
    odd_word = byte_to_odd_word[os.urandom(1)]
    even_word = byte_to_even_word[os.urandom(1)]
    return "%d-%s-%s" % (channel_id, odd_word.lower(), even_word.lower())

def extract_channel_id(code):
    channel_id = int(code.split("-")[0])
    return channel_id

import readline
#import sys

class CodeInputter:
    def __init__(self, channel_ids):
        self.channel_ids = channel_ids
        self.last_text = None # memoize for a speedup
        self.last_matches = None

    def wrap_completer(self, text, state):
        try:
            return self.completer(text, state)
        except Exception as e:
            # completer exceptions are normally silently discarded, which
            # makes debugging challenging
            print("completer exception: %s" % e)
            raise e

    def completer(self, text, state):
        #print("completer:", text, state, file=sys.stderr)
        pieces = text.split("-")
        last = pieces[-1].lower()
        if text == self.last_text:
            matches = self.last_matches
            #print(" old matches", len(matches), file=sys.stderr)
        else:
            if len(pieces) < 2:
                matches = [str(channel_id) for channel_id in self.channel_ids
                           if str(channel_id).startswith(last)]
            else:
                if len(pieces) % 2 == 0:
                    words = odd_words_lowercase
                else:
                    words = even_words_lowercase
                so_far = "-".join(pieces[:-1]) + "-"
                matches = sorted([so_far+word for word in words
                                  if word.startswith(last)])
            self.last_text = text
            self.last_matches = matches
            #print(" new matches:", matches, file=sys.stderr)
        if state >= len(matches):
            return None
        match = matches[state]
        if len(pieces) < 3:
            match += "-"
        return match


def input_code_with_completion(prompt, channel_ids):
    c = CodeInputter(channel_ids)
    readline.parse_and_bind("tab: complete")
    readline.set_completer(c.wrap_completer)
    readline.set_completer_delims("")
    code = raw_input(prompt)
    return code

if __name__ == "__main__":
    code = input_code_with_completion("Enter wormhole code: ", [])
    print("code is:", code)
