How to Prepare a Release
========================

-  create a branch: git checkout main ; git pull ; git checkout -b
   prepare-release

-  Re-export completions: make completions ; git add
   wormhole_complete.\* ; git commit -m “completions”
   (this step requires installed bash, zsh, and fish)

-  Update NEWS.md (copy-edit, add missing credits, etc)

-  make release
   (this step requires having libgpgme installed, and will GPG sign the package )
   then
   - push your branch to the repo!
   - make a pull request to the repo from that branch!
   - wait for CI to finish on your branch!

-  make release-test
   this will
   - verify your GPG signature (debian *cares*, PyPI does not)
   - create a venv and install both packages (source and wheel) in that venv
   - run the tests

-  make release-upload
   this will
   - git add signatures in ./signatures
