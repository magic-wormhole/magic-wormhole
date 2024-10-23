# How to Make a Release
# ---------------------
#
# This file answers the question "how to make a release" hopefully
# better than a document does (only meejah and warner may currently do
# the "upload to PyPI" part anyway)
#

default:
	echo "see Makefile"

completions:
	bash -c '_WORMHOLE_COMPLETE=bash_source wormhole > wormhole_complete.bash'
	zsh -c '_WORMHOLE_COMPLETE=zsh_source wormhole > wormhole_complete.zsh'
	fish -c '_WORMHOLE_COMPLETE=fish_source wormhole > wormhole_complete.fish'

release-clean:
	@echo "Cleanup stale release: " `python newest-version.py`
	-rm NEWS.md.asc
	rm dist/magic[_-]wormhole-`python newest-version.py`.tar.gz*
	rm dist/magic_wormhole-`python newest-version.py`-py3-none-any.whl*
	git tag -d `python newest-version.py`

# create a branch, like: git checkout -b prepare-release-0.16.0
# then run these, so CI can run on the release
release:
	@echo "Is checkout clean?"
	git diff-files --quiet
	git diff-index --quiet --cached HEAD --

	@echo "Install required build software"
	python3 -m pip install --editable .[build]

	@echo "Test README"
	python3 setup.py check -r -s

	@echo "Is GPG Agent running, and has key?"
	gpg --pinentry=loopback -u meejah@meejah.ca --armor --clear-sign NEWS.md

	@echo "Bump version and create tag"
	python3 update-version.py
#	python3 update-version.py --patch  # for bugfix release

	@echo "Build and sign wheel"
	python3 setup.py bdist_wheel
	gpg --pinentry=loopback -u meejah@meejah.ca --armor --detach-sign dist/magic_wormhole-`git describe --abbrev=0`-py3-none-any.whl
	ls dist/*`git describe --abbrev=0`*

	@echo "Build and sign source-dist"
	python3 setup.py sdist
	gpg --pinentry=loopback -u meejah@meejah.ca --armor --detach-sign dist/magic_wormhole-`git describe --abbrev=0`.tar.gz
	ls dist/*`git describe --abbrev=0`*

release-test:
	gpg --verify dist/magic_wormhole-`git describe --abbrev=0`.tar.gz.asc
	gpg --verify dist/magic_wormhole-`git describe --abbrev=0`-py3-none-any.whl.asc
	python -m venv testmf_venv
	testmf_venv/bin/pip install --upgrade pip
	testmf_venv/bin/pip install dist/magic_wormhole-`git describe --abbrev=0`-py3-none-any.whl
	testmf_venv/bin/wormhole --version
	testmf_venv/bin/pip uninstall -y magic_wormhole
	testmf_venv/bin/pip install dist/magic_wormhole-`git describe --abbrev=0`.tar.gz
	testmf_venv/bin/wormhole --version
	rm -rf testmf_venv

release-upload:
	twine upload --username __token__ --password `cat PRIVATE-release-token` dist/magic_wormhole-`git describe --abbrev=0`-py3-none-any.whl dist/magic_wormhole-`git describe --abbrev=0`-py3-none-any.whl.asc dist/magic_wormhole-`git describe --abbrev=0`.tar.gz dist/magic_wormhole-`git describe --abbrev=0`.tar.gz.asc
	mv dist/*-`git describe --abbrev=0`.tar.gz.asc signatures/
	mv dist/*-`git describe --abbrev=0`-py3-none-any.whl.asc signatures/
	git add signatures/magic_wormhole-`git describe --abbrev=0`.tar.gz.asc
	git add signatures/magic_wormhole-`git describe --abbrev=0`-py3-none-any.whl.asc
	git commit -m "signatures for release"
	git push origin-push `git describe --abbrev=0`


dilation.png: dilation.seqdiag
	seqdiag --no-transparency -T png --size 1000x800 -o dilation.png
