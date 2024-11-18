How to Prepare a Release
========================

-  create a branch: git checkout main ; git pull ; git checkout -b
   prepare-release

-  Re-export completions: make completions ; git add
   wormhole_complete.\* ; git commit -m “completions”

-  Update NEWS.md (copy-edit, add missing credits, etc)

-  make release

-  make release-test

-  make release-upload
