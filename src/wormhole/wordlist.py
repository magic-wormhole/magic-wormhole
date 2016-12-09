from __future__ import unicode_literals
# The PGP Word List, which maps bytes to phonetically-distinct words. There
# are two lists, even and odd, and encodings should alternate between then to
# detect dropped words. https://en.wikipedia.org/wiki/PGP_Words

# Thanks to Warren Guy for transcribing them:
# https://github.com/warrenguy/javascript-pgp-word-list

from binascii import unhexlify

raw_words = {
'00': ['aardvark', 'adroitness'], '01': ['absurd', 'adviser'],
'02': ['accrue', 'aftermath'], '03': ['acme', 'aggregate'],
'04': ['adrift', 'alkali'], '05': ['adult', 'almighty'],
'06': ['afflict', 'amulet'], '07': ['ahead', 'amusement'],
'08': ['aimless', 'antenna'], '09': ['Algol', 'applicant'],
'0A': ['allow', 'Apollo'], '0B': ['alone', 'armistice'],
'0C': ['ammo', 'article'], '0D': ['ancient', 'asteroid'],
'0E': ['apple', 'Atlantic'], '0F': ['artist', 'atmosphere'],
'10': ['assume', 'autopsy'], '11': ['Athens', 'Babylon'],
'12': ['atlas', 'backwater'], '13': ['Aztec', 'barbecue'],
'14': ['baboon', 'belowground'], '15': ['backfield', 'bifocals'],
'16': ['backward', 'bodyguard'], '17': ['banjo', 'bookseller'],
'18': ['beaming', 'borderline'], '19': ['bedlamp', 'bottomless'],
'1A': ['beehive', 'Bradbury'], '1B': ['beeswax', 'bravado'],
'1C': ['befriend', 'Brazilian'], '1D': ['Belfast', 'breakaway'],
'1E': ['berserk', 'Burlington'], '1F': ['billiard', 'businessman'],
'20': ['bison', 'butterfat'], '21': ['blackjack', 'Camelot'],
'22': ['blockade', 'candidate'], '23': ['blowtorch', 'cannonball'],
'24': ['bluebird', 'Capricorn'], '25': ['bombast', 'caravan'],
'26': ['bookshelf', 'caretaker'], '27': ['brackish', 'celebrate'],
'28': ['breadline', 'cellulose'], '29': ['breakup', 'certify'],
'2A': ['brickyard', 'chambermaid'], '2B': ['briefcase', 'Cherokee'],
'2C': ['Burbank', 'Chicago'], '2D': ['button', 'clergyman'],
'2E': ['buzzard', 'coherence'], '2F': ['cement', 'combustion'],
'30': ['chairlift', 'commando'], '31': ['chatter', 'company'],
'32': ['checkup', 'component'], '33': ['chisel', 'concurrent'],
'34': ['choking', 'confidence'], '35': ['chopper', 'conformist'],
'36': ['Christmas', 'congregate'], '37': ['clamshell', 'consensus'],
'38': ['classic', 'consulting'], '39': ['classroom', 'corporate'],
'3A': ['cleanup', 'corrosion'], '3B': ['clockwork', 'councilman'],
'3C': ['cobra', 'crossover'], '3D': ['commence', 'crucifix'],
'3E': ['concert', 'cumbersome'], '3F': ['cowbell', 'customer'],
'40': ['crackdown', 'Dakota'], '41': ['cranky', 'decadence'],
'42': ['crowfoot', 'December'], '43': ['crucial', 'decimal'],
'44': ['crumpled', 'designing'], '45': ['crusade', 'detector'],
'46': ['cubic', 'detergent'], '47': ['dashboard', 'determine'],
'48': ['deadbolt', 'dictator'], '49': ['deckhand', 'dinosaur'],
'4A': ['dogsled', 'direction'], '4B': ['dragnet', 'disable'],
'4C': ['drainage', 'disbelief'], '4D': ['dreadful', 'disruptive'],
'4E': ['drifter', 'distortion'], '4F': ['dropper', 'document'],
'50': ['drumbeat', 'embezzle'], '51': ['drunken', 'enchanting'],
'52': ['Dupont', 'enrollment'], '53': ['dwelling', 'enterprise'],
'54': ['eating', 'equation'], '55': ['edict', 'equipment'],
'56': ['egghead', 'escapade'], '57': ['eightball', 'Eskimo'],
'58': ['endorse', 'everyday'], '59': ['endow', 'examine'],
'5A': ['enlist', 'existence'], '5B': ['erase', 'exodus'],
'5C': ['escape', 'fascinate'], '5D': ['exceed', 'filament'],
'5E': ['eyeglass', 'finicky'], '5F': ['eyetooth', 'forever'],
'60': ['facial', 'fortitude'], '61': ['fallout', 'frequency'],
'62': ['flagpole', 'gadgetry'], '63': ['flatfoot', 'Galveston'],
'64': ['flytrap', 'getaway'], '65': ['fracture', 'glossary'],
'66': ['framework', 'gossamer'], '67': ['freedom', 'graduate'],
'68': ['frighten', 'gravity'], '69': ['gazelle', 'guitarist'],
'6A': ['Geiger', 'hamburger'], '6B': ['glitter', 'Hamilton'],
'6C': ['glucose', 'handiwork'], '6D': ['goggles', 'hazardous'],
'6E': ['goldfish', 'headwaters'], '6F': ['gremlin', 'hemisphere'],
'70': ['guidance', 'hesitate'], '71': ['hamlet', 'hideaway'],
'72': ['highchair', 'holiness'], '73': ['hockey', 'hurricane'],
'74': ['indoors', 'hydraulic'], '75': ['indulge', 'impartial'],
'76': ['inverse', 'impetus'], '77': ['involve', 'inception'],
'78': ['island', 'indigo'], '79': ['jawbone', 'inertia'],
'7A': ['keyboard', 'infancy'], '7B': ['kickoff', 'inferno'],
'7C': ['kiwi', 'informant'], '7D': ['klaxon', 'insincere'],
'7E': ['locale', 'insurgent'], '7F': ['lockup', 'integrate'],
'80': ['merit', 'intention'], '81': ['minnow', 'inventive'],
'82': ['miser', 'Istanbul'], '83': ['Mohawk', 'Jamaica'],
'84': ['mural', 'Jupiter'], '85': ['music', 'leprosy'],
'86': ['necklace', 'letterhead'], '87': ['Neptune', 'liberty'],
'88': ['newborn', 'maritime'], '89': ['nightbird', 'matchmaker'],
'8A': ['Oakland', 'maverick'], '8B': ['obtuse', 'Medusa'],
'8C': ['offload', 'megaton'], '8D': ['optic', 'microscope'],
'8E': ['orca', 'microwave'], '8F': ['payday', 'midsummer'],
'90': ['peachy', 'millionaire'], '91': ['pheasant', 'miracle'],
'92': ['physique', 'misnomer'], '93': ['playhouse', 'molasses'],
'94': ['Pluto', 'molecule'], '95': ['preclude', 'Montana'],
'96': ['prefer', 'monument'], '97': ['preshrunk', 'mosquito'],
'98': ['printer', 'narrative'], '99': ['prowler', 'nebula'],
'9A': ['pupil', 'newsletter'], '9B': ['puppy', 'Norwegian'],
'9C': ['python', 'October'], '9D': ['quadrant', 'Ohio'],
'9E': ['quiver', 'onlooker'], '9F': ['quota', 'opulent'],
'A0': ['ragtime', 'Orlando'], 'A1': ['ratchet', 'outfielder'],
'A2': ['rebirth', 'Pacific'], 'A3': ['reform', 'pandemic'],
'A4': ['regain', 'Pandora'], 'A5': ['reindeer', 'paperweight'],
'A6': ['rematch', 'paragon'], 'A7': ['repay', 'paragraph'],
'A8': ['retouch', 'paramount'], 'A9': ['revenge', 'passenger'],
'AA': ['reward', 'pedigree'], 'AB': ['rhythm', 'Pegasus'],
'AC': ['ribcage', 'penetrate'], 'AD': ['ringbolt', 'perceptive'],
'AE': ['robust', 'performance'], 'AF': ['rocker', 'pharmacy'],
'B0': ['ruffled', 'phonetic'], 'B1': ['sailboat', 'photograph'],
'B2': ['sawdust', 'pioneer'], 'B3': ['scallion', 'pocketful'],
'B4': ['scenic', 'politeness'], 'B5': ['scorecard', 'positive'],
'B6': ['Scotland', 'potato'], 'B7': ['seabird', 'processor'],
'B8': ['select', 'provincial'], 'B9': ['sentence', 'proximate'],
'BA': ['shadow', 'puberty'], 'BB': ['shamrock', 'publisher'],
'BC': ['showgirl', 'pyramid'], 'BD': ['skullcap', 'quantity'],
'BE': ['skydive', 'racketeer'], 'BF': ['slingshot', 'rebellion'],
'C0': ['slowdown', 'recipe'], 'C1': ['snapline', 'recover'],
'C2': ['snapshot', 'repellent'], 'C3': ['snowcap', 'replica'],
'C4': ['snowslide', 'reproduce'], 'C5': ['solo', 'resistor'],
'C6': ['southward', 'responsive'], 'C7': ['soybean', 'retraction'],
'C8': ['spaniel', 'retrieval'], 'C9': ['spearhead', 'retrospect'],
'CA': ['spellbind', 'revenue'], 'CB': ['spheroid', 'revival'],
'CC': ['spigot', 'revolver'], 'CD': ['spindle', 'sandalwood'],
'CE': ['spyglass', 'sardonic'], 'CF': ['stagehand', 'Saturday'],
'D0': ['stagnate', 'savagery'], 'D1': ['stairway', 'scavenger'],
'D2': ['standard', 'sensation'], 'D3': ['stapler', 'sociable'],
'D4': ['steamship', 'souvenir'], 'D5': ['sterling', 'specialist'],
'D6': ['stockman', 'speculate'], 'D7': ['stopwatch', 'stethoscope'],
'D8': ['stormy', 'stupendous'], 'D9': ['sugar', 'supportive'],
'DA': ['surmount', 'surrender'], 'DB': ['suspense', 'suspicious'],
'DC': ['sweatband', 'sympathy'], 'DD': ['swelter', 'tambourine'],
'DE': ['tactics', 'telephone'], 'DF': ['talon', 'therapist'],
'E0': ['tapeworm', 'tobacco'], 'E1': ['tempest', 'tolerance'],
'E2': ['tiger', 'tomorrow'], 'E3': ['tissue', 'torpedo'],
'E4': ['tonic', 'tradition'], 'E5': ['topmost', 'travesty'],
'E6': ['tracker', 'trombonist'], 'E7': ['transit', 'truncated'],
'E8': ['trauma', 'typewriter'], 'E9': ['treadmill', 'ultimate'],
'EA': ['Trojan', 'undaunted'], 'EB': ['trouble', 'underfoot'],
'EC': ['tumor', 'unicorn'], 'ED': ['tunnel', 'unify'],
'EE': ['tycoon', 'universe'], 'EF': ['uncut', 'unravel'],
'F0': ['unearth', 'upcoming'], 'F1': ['unwind', 'vacancy'],
'F2': ['uproot', 'vagabond'], 'F3': ['upset', 'vertigo'],
'F4': ['upshot', 'Virginia'], 'F5': ['vapor', 'visitor'],
'F6': ['village', 'vocalist'], 'F7': ['virus', 'voyager'],
'F8': ['Vulcan', 'warranty'], 'F9': ['waffle', 'Waterloo'],
'FA': ['wallet', 'whimsical'], 'FB': ['watchword', 'Wichita'],
'FC': ['wayside', 'Wilmington'], 'FD': ['willow', 'Wyoming'],
'FE': ['woodlark', 'yesteryear'], 'FF': ['Zulu', 'Yucatan']
};

byte_to_even_word = dict([(unhexlify(k.encode("ascii")), both_words[0])
                          for k,both_words
                          in raw_words.items()])

byte_to_odd_word = dict([(unhexlify(k.encode("ascii")), both_words[1])
                         for k,both_words
                         in raw_words.items()])
even_words_lowercase, odd_words_lowercase = set(), set()
even_words_lowercase_to_byte, odd_words_lowercase_to_byte = dict(), dict()
for k,both_words in raw_words.items():
    even_word, odd_word = both_words

    even_words_lowercase.add(even_word.lower())
    even_words_lowercase_to_byte[even_word.lower()] = unhexlify(k.encode("ascii"))

    odd_words_lowercase.add(odd_word.lower())
    odd_words_lowercase_to_byte[odd_word.lower()] = unhexlify(k.encode("ascii"))
