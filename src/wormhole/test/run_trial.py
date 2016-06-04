from __future__ import unicode_literals
# This is a tiny helper module, to let "python -m wormhole.test.run_trial
# ARGS" does the same thing as running "trial ARGS" (unfortunately
# twisted/scripts/trial.py does not have a '__name__=="__main__"' clause).
#
# This makes it easier to run trial under coverage from tox:
# * "coverage run trial ARGS" is how you'd usually do it
# * but "trial" must be the one in tox's virtualenv
# * "coverage run `which trial` ARGS" works from a shell
# * but tox doesn't use a shell
# So use:
#  "coverage run -m wormhole.test.run_trial ARGS"

from twisted.scripts.trial import run

run()
