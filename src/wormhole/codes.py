from __future__ import print_function
import os, sys
from .wordlist import (byte_to_even_word, byte_to_odd_word,
                       even_words_lowercase, odd_words_lowercase)

PY2 = sys.version_info[0] == 2
if PY2:
    input = raw_input

def make_code(channel_id, code_length):
    words = []
    for i in range(code_length):
        # we start with an "odd word"
        if i % 2 == 0:
            words.append(byte_to_odd_word[os.urandom(1)].lower())
        else:
            words.append(byte_to_even_word[os.urandom(1)].lower())
    return str(channel_id) + "-" + "-".join(words)

def extract_channel_id(code):
    channel_id = int(code.split("-")[0])
    return channel_id

import readline
#import sys

class CodeInputter:
    def __init__(self, get_channel_ids, code_length):
        self.get_channel_ids = get_channel_ids
        self.code_length = code_length
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
        #if state == 0:
        #    print("", file=sys.stderr)
        #print("completer: '%s' %d '%d'" % (text, state,
        #                                   readline.get_completion_type()),
        #      file=sys.stderr)
        #sys.stderr.flush()
        pieces = text.split("-")
        last = pieces[-1].lower()
        if text == self.last_text and len(pieces) >= 2:
            # if len(pieces) == 1, skip the cache, so we can re-fetch the
            # channel_id list
            matches = self.last_matches
            #print(" old matches", len(matches), file=sys.stderr)
        else:
            if len(pieces) <= 1:
                channel_ids = self.get_channel_ids()
                matches = [str(channel_id) for channel_id in channel_ids
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
        if len(pieces) < 1+self.code_length:
            match += "-"
        #print(" match: '%s'" % match, file=sys.stderr)
        #sys.stderr.flush()
        return match


def input_code_with_completion(prompt, get_channel_ids, code_length):
    c = CodeInputter(get_channel_ids, code_length)
    readline.parse_and_bind("tab: complete")
    readline.set_completer(c.wrap_completer)
    readline.set_completer_delims("")
    code = input(prompt)
    return code

if __name__ == "__main__":
    code = input_code_with_completion("Enter wormhole code: ", lambda: [], 2)
    print("code is:", code)
