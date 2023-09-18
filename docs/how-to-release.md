How to Prepare a Release
========================

* create a branch: git checkout main ; git pull ; git checkout -b prepare-release

* Re-export the Bash completions into `wormhole_complete.bash`.

* Update NEWS.md (copy-edit, add missing credits, etc)

* make release

* make release-test

* make release-upload
