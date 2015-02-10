
# Version: 0.12

"""
The Versioneer
==============

* like a rocketeer, but for versions!
* https://github.com/warner/python-versioneer
* Brian Warner
* License: Public Domain
* Compatible With: python2.6, 2.7, 3.2, 3.3, 3.4, and pypy

[![Build Status](https://travis-ci.org/warner/python-versioneer.png?branch=master)](https://travis-ci.org/warner/python-versioneer)

This is a tool for managing a recorded version number in distutils-based
python projects. The goal is to remove the tedious and error-prone "update
the embedded version string" step from your release process. Making a new
release should be as easy as recording a new tag in your version-control
system, and maybe making new tarballs.


## Quick Install

* `pip install versioneer` to somewhere to your $PATH
* run `versioneer-installer` in your source tree: this installs `versioneer.py`
* follow the instructions below (also in the `versioneer.py` docstring)

## Version Identifiers

Source trees come from a variety of places:

* a version-control system checkout (mostly used by developers)
* a nightly tarball, produced by build automation
* a snapshot tarball, produced by a web-based VCS browser, like github's
  "tarball from tag" feature
* a release tarball, produced by "setup.py sdist", distributed through PyPI

Within each source tree, the version identifier (either a string or a number,
this tool is format-agnostic) can come from a variety of places:

* ask the VCS tool itself, e.g. "git describe" (for checkouts), which knows
  about recent "tags" and an absolute revision-id
* the name of the directory into which the tarball was unpacked
* an expanded VCS keyword ($Id$, etc)
* a `_version.py` created by some earlier build step

For released software, the version identifier is closely related to a VCS
tag. Some projects use tag names that include more than just the version
string (e.g. "myproject-1.2" instead of just "1.2"), in which case the tool
needs to strip the tag prefix to extract the version identifier. For
unreleased software (between tags), the version identifier should provide
enough information to help developers recreate the same tree, while also
giving them an idea of roughly how old the tree is (after version 1.2, before
version 1.3). Many VCS systems can report a description that captures this,
for example 'git describe --tags --dirty --always' reports things like
"0.7-1-g574ab98-dirty" to indicate that the checkout is one revision past the
0.7 tag, has a unique revision id of "574ab98", and is "dirty" (it has
uncommitted changes.

The version identifier is used for multiple purposes:

* to allow the module to self-identify its version: `myproject.__version__`
* to choose a name and prefix for a 'setup.py sdist' tarball

## Theory of Operation

Versioneer works by adding a special `_version.py` file into your source
tree, where your `__init__.py` can import it. This `_version.py` knows how to
dynamically ask the VCS tool for version information at import time. However,
when you use "setup.py build" or "setup.py sdist", `_version.py` in the new
copy is replaced by a small static file that contains just the generated
version data.

`_version.py` also contains `$Revision$` markers, and the installation
process marks `_version.py` to have this marker rewritten with a tag name
during the "git archive" command. As a result, generated tarballs will
contain enough information to get the proper version.


## Installation

First, decide on values for the following configuration variables:

* `VCS`: the version control system you use. Currently accepts "git".

* `versionfile_source`:

  A project-relative pathname into which the generated version strings should
  be written. This is usually a `_version.py` next to your project's main
  `__init__.py` file, so it can be imported at runtime. If your project uses
  `src/myproject/__init__.py`, this should be `src/myproject/_version.py`.
  This file should be checked in to your VCS as usual: the copy created below
  by `setup.py versioneer` will include code that parses expanded VCS
  keywords in generated tarballs. The 'build' and 'sdist' commands will
  replace it with a copy that has just the calculated version string.

  This must be set even if your project does not have any modules (and will
  therefore never import `_version.py`), since "setup.py sdist" -based trees
  still need somewhere to record the pre-calculated version strings. Anywhere
  in the source tree should do. If there is a `__init__.py` next to your
  `_version.py`, the `setup.py versioneer` command (described below) will
  append some `__version__`-setting assignments, if they aren't already
  present.

*  `versionfile_build`:

  Like `versionfile_source`, but relative to the build directory instead of
  the source directory. These will differ when your setup.py uses
  'package_dir='. If you have `package_dir={'myproject': 'src/myproject'}`,
  then you will probably have `versionfile_build='myproject/_version.py'` and
  `versionfile_source='src/myproject/_version.py'`.

  If this is set to None, then `setup.py build` will not attempt to rewrite
  any `_version.py` in the built tree. If your project does not have any
  libraries (e.g. if it only builds a script), then you should use
  `versionfile_build = None` and override `distutils.command.build_scripts`
  to explicitly insert a copy of `versioneer.get_version()` into your
  generated script.

* `tag_prefix`:

  a string, like 'PROJECTNAME-', which appears at the start of all VCS tags.
  If your tags look like 'myproject-1.2.0', then you should use
  tag_prefix='myproject-'. If you use unprefixed tags like '1.2.0', this
  should be an empty string.

* `parentdir_prefix`:

  a string, frequently the same as tag_prefix, which appears at the start of
  all unpacked tarball filenames. If your tarball unpacks into
  'myproject-1.2.0', this should be 'myproject-'.

This tool provides one script, named `versioneer-installer`. That script does
one thing: write a copy of `versioneer.py` into the current directory.

To versioneer-enable your project:

* 1: Run `versioneer-installer` to copy `versioneer.py` into the top of your
  source tree.

* 2: add the following lines to the top of your `setup.py`, with the
  configuration values you decided earlier:

        import versioneer
        versioneer.VCS = 'git'
        versioneer.versionfile_source = 'src/myproject/_version.py'
        versioneer.versionfile_build = 'myproject/_version.py'
        versioneer.tag_prefix = '' # tags are like 1.2.0
        versioneer.parentdir_prefix = 'myproject-' # dirname like 'myproject-1.2.0'

* 3: add the following arguments to the setup() call in your setup.py:

        version=versioneer.get_version(),
        cmdclass=versioneer.get_cmdclass(),

* 4: now run `setup.py versioneer`, which will create `_version.py`, and will
  modify your `__init__.py` (if one exists next to `_version.py`) to define
  `__version__` (by calling a function from `_version.py`). It will also
  modify your `MANIFEST.in` to include both `versioneer.py` and the generated
  `_version.py` in sdist tarballs.

* 5: commit these changes to your VCS. To make sure you won't forget,
  `setup.py versioneer` will mark everything it touched for addition.

## Post-Installation Usage

Once established, all uses of your tree from a VCS checkout should get the
current version string. All generated tarballs should include an embedded
version string (so users who unpack them will not need a VCS tool installed).

If you distribute your project through PyPI, then the release process should
boil down to two steps:

* 1: git tag 1.0
* 2: python setup.py register sdist upload

If you distribute it through github (i.e. users use github to generate
tarballs with `git archive`), the process is:

* 1: git tag 1.0
* 2: git push; git push --tags

Currently, all version strings must be based upon a tag. Versioneer will
report "unknown" until your tree has at least one tag in its history. This
restriction will be fixed eventually (see issue #12).

## Version-String Flavors

Code which uses Versioneer can learn about its version string at runtime by
importing `_version` from your main `__init__.py` file and running the
`get_versions()` function. From the "outside" (e.g. in `setup.py`), you can
import the top-level `versioneer.py` and run `get_versions()`.

Both functions return a dictionary with different keys for different flavors
of the version string:

* `['version']`: condensed tag+distance+shortid+dirty identifier. For git,
  this uses the output of `git describe --tags --dirty --always` but strips
  the tag_prefix. For example "0.11-2-g1076c97-dirty" indicates that the tree
  is like the "1076c97" commit but has uncommitted changes ("-dirty"), and
  that this commit is two revisions ("-2-") beyond the "0.11" tag. For
  released software (exactly equal to a known tag), the identifier will only
  contain the stripped tag, e.g. "0.11".

* `['full']`: detailed revision identifier. For Git, this is the full SHA1
  commit id, followed by "-dirty" if the tree contains uncommitted changes,
  e.g. "1076c978a8d3cfc70f408fe5974aa6c092c949ac-dirty".

Some variants are more useful than others. Including `full` in a bug report
should allow developers to reconstruct the exact code being tested (or
indicate the presence of local changes that should be shared with the
developers). `version` is suitable for display in an "about" box or a CLI
`--version` output: it can be easily compared against release notes and lists
of bugs fixed in various releases.

In the future, this will also include a
[PEP-0440](http://legacy.python.org/dev/peps/pep-0440/) -compatible flavor
(e.g. `1.2.post0.dev123`). This loses a lot of information (and has no room
for a hash-based revision id), but is safe to use in a `setup.py`
"`version=`" argument. It also enables tools like *pip* to compare version
strings and evaluate compatibility constraint declarations.

The `setup.py versioneer` command adds the following text to your
`__init__.py` to place a basic version in `YOURPROJECT.__version__`:

    from ._version import get_versions
    __version__ = get_versions()['version']
    del get_versions

## Updating Versioneer

To upgrade your project to a new release of Versioneer, do the following:

* install the new Versioneer (`pip install -U versioneer` or equivalent)
* re-run `versioneer-installer` in your source tree to replace your copy of
  `versioneer.py`
* edit `setup.py`, if necessary, to include any new configuration settings
  indicated by the release notes
* re-run `setup.py versioneer` to replace `SRC/_version.py`
* commit any changed files

### Upgrading from 0.10 to 0.11

You must add a `versioneer.VCS = "git"` to your `setup.py` before re-running
`setup.py versioneer`. This will enable the use of additional version-control
systems (SVN, etc) in the future.

### Upgrading from 0.11 to 0.12

Nothing special.

## Future Directions

This tool is designed to make it easily extended to other version-control
systems: all VCS-specific components are in separate directories like
src/git/ . The top-level `versioneer.py` script is assembled from these
components by running make-versioneer.py . In the future, make-versioneer.py
will take a VCS name as an argument, and will construct a version of
`versioneer.py` that is specific to the given VCS. It might also take the
configuration arguments that are currently provided manually during
installation by editing setup.py . Alternatively, it might go the other
direction and include code from all supported VCS systems, reducing the
number of intermediate scripts.


## License

To make Versioneer easier to embed, all its code is hereby released into the
public domain. The `_version.py` that it creates is also in the public
domain.

"""

import os, sys, re, subprocess, errno
from distutils.core import Command
from distutils.command.sdist import sdist as _sdist
from distutils.command.build import build as _build

# these configuration settings will be overridden by setup.py after it
# imports us
versionfile_source = None
versionfile_build = None
tag_prefix = None
parentdir_prefix = None
VCS = None

# these dictionaries contain VCS-specific tools
LONG_VERSION_PY = {}

def run_command(commands, args, cwd=None, verbose=False, hide_stderr=False):
    assert isinstance(commands, list)
    p = None
    for c in commands:
        try:
            # remember shell=False, so use git.cmd on windows, not just git
            p = subprocess.Popen([c] + args, cwd=cwd, stdout=subprocess.PIPE,
                                 stderr=(subprocess.PIPE if hide_stderr
                                         else None))
            break
        except EnvironmentError:
            e = sys.exc_info()[1]
            if e.errno == errno.ENOENT:
                continue
            if verbose:
                print("unable to run %s" % args[0])
                print(e)
            return None
    else:
        if verbose:
            print("unable to find command, tried %s" % (commands,))
        return None
    stdout = p.communicate()[0].strip()
    if sys.version >= '3':
        stdout = stdout.decode()
    if p.returncode != 0:
        if verbose:
            print("unable to run %s (error)" % args[0])
        return None
    return stdout

LONG_VERSION_PY['git'] = '''
# This file helps to compute a version number in source trees obtained from
# git-archive tarball (such as those provided by githubs download-from-tag
# feature). Distribution tarballs (built by setup.py sdist) and build
# directories (produced by setup.py build) will contain a much shorter file
# that just contains the computed version number.

# This file is released into the public domain. Generated by
# versioneer-0.12 (https://github.com/warner/python-versioneer)

# these strings will be replaced by git during git-archive
git_refnames = "%(DOLLAR)sFormat:%%d%(DOLLAR)s"
git_full = "%(DOLLAR)sFormat:%%H%(DOLLAR)s"

# these strings are filled in when 'setup.py versioneer' creates _version.py
tag_prefix = "%(TAG_PREFIX)s"
parentdir_prefix = "%(PARENTDIR_PREFIX)s"
versionfile_source = "%(VERSIONFILE_SOURCE)s"

import os, sys, re, subprocess, errno

def run_command(commands, args, cwd=None, verbose=False, hide_stderr=False):
    assert isinstance(commands, list)
    p = None
    for c in commands:
        try:
            # remember shell=False, so use git.cmd on windows, not just git
            p = subprocess.Popen([c] + args, cwd=cwd, stdout=subprocess.PIPE,
                                 stderr=(subprocess.PIPE if hide_stderr
                                         else None))
            break
        except EnvironmentError:
            e = sys.exc_info()[1]
            if e.errno == errno.ENOENT:
                continue
            if verbose:
                print("unable to run %%s" %% args[0])
                print(e)
            return None
    else:
        if verbose:
            print("unable to find command, tried %%s" %% (commands,))
        return None
    stdout = p.communicate()[0].strip()
    if sys.version >= '3':
        stdout = stdout.decode()
    if p.returncode != 0:
        if verbose:
            print("unable to run %%s (error)" %% args[0])
        return None
    return stdout


def versions_from_parentdir(parentdir_prefix, root, verbose=False):
    # Source tarballs conventionally unpack into a directory that includes
    # both the project name and a version string.
    dirname = os.path.basename(root)
    if not dirname.startswith(parentdir_prefix):
        if verbose:
            print("guessing rootdir is '%%s', but '%%s' doesn't start with prefix '%%s'" %%
                  (root, dirname, parentdir_prefix))
        return None
    return {"version": dirname[len(parentdir_prefix):], "full": ""}

def git_get_keywords(versionfile_abs):
    # the code embedded in _version.py can just fetch the value of these
    # keywords. When used from setup.py, we don't want to import _version.py,
    # so we do it with a regexp instead. This function is not used from
    # _version.py.
    keywords = {}
    try:
        f = open(versionfile_abs,"r")
        for line in f.readlines():
            if line.strip().startswith("git_refnames ="):
                mo = re.search(r'=\s*"(.*)"', line)
                if mo:
                    keywords["refnames"] = mo.group(1)
            if line.strip().startswith("git_full ="):
                mo = re.search(r'=\s*"(.*)"', line)
                if mo:
                    keywords["full"] = mo.group(1)
        f.close()
    except EnvironmentError:
        pass
    return keywords

def git_versions_from_keywords(keywords, tag_prefix, verbose=False):
    if not keywords:
        return {} # keyword-finding function failed to find keywords
    refnames = keywords["refnames"].strip()
    if refnames.startswith("$Format"):
        if verbose:
            print("keywords are unexpanded, not using")
        return {} # unexpanded, so not in an unpacked git-archive tarball
    refs = set([r.strip() for r in refnames.strip("()").split(",")])
    # starting in git-1.8.3, tags are listed as "tag: foo-1.0" instead of
    # just "foo-1.0". If we see a "tag: " prefix, prefer those.
    TAG = "tag: "
    tags = set([r[len(TAG):] for r in refs if r.startswith(TAG)])
    if not tags:
        # Either we're using git < 1.8.3, or there really are no tags. We use
        # a heuristic: assume all version tags have a digit. The old git %%d
        # expansion behaves like git log --decorate=short and strips out the
        # refs/heads/ and refs/tags/ prefixes that would let us distinguish
        # between branches and tags. By ignoring refnames without digits, we
        # filter out many common branch names like "release" and
        # "stabilization", as well as "HEAD" and "master".
        tags = set([r for r in refs if re.search(r'\d', r)])
        if verbose:
            print("discarding '%%s', no digits" %% ",".join(refs-tags))
    if verbose:
        print("likely tags: %%s" %% ",".join(sorted(tags)))
    for ref in sorted(tags):
        # sorting will prefer e.g. "2.0" over "2.0rc1"
        if ref.startswith(tag_prefix):
            r = ref[len(tag_prefix):]
            if verbose:
                print("picking %%s" %% r)
            return { "version": r,
                     "full": keywords["full"].strip() }
    # no suitable tags, so we use the full revision id
    if verbose:
        print("no suitable tags, using full revision id")
    return { "version": keywords["full"].strip(),
             "full": keywords["full"].strip() }


def git_versions_from_vcs(tag_prefix, root, verbose=False):
    # this runs 'git' from the root of the source tree. This only gets called
    # if the git-archive 'subst' keywords were *not* expanded, and
    # _version.py hasn't already been rewritten with a short version string,
    # meaning we're inside a checked out source tree.

    if not os.path.exists(os.path.join(root, ".git")):
        if verbose:
            print("no .git in %%s" %% root)
        return {}

    GITS = ["git"]
    if sys.platform == "win32":
        GITS = ["git.cmd", "git.exe"]
    stdout = run_command(GITS, ["describe", "--tags", "--dirty", "--always"],
                         cwd=root)
    if stdout is None:
        return {}
    if not stdout.startswith(tag_prefix):
        if verbose:
            print("tag '%%s' doesn't start with prefix '%%s'" %% (stdout, tag_prefix))
        return {}
    tag = stdout[len(tag_prefix):]
    stdout = run_command(GITS, ["rev-parse", "HEAD"], cwd=root)
    if stdout is None:
        return {}
    full = stdout.strip()
    if tag.endswith("-dirty"):
        full += "-dirty"
    return {"version": tag, "full": full}


def get_versions(default={"version": "unknown", "full": ""}, verbose=False):
    # I am in _version.py, which lives at ROOT/VERSIONFILE_SOURCE. If we have
    # __file__, we can work backwards from there to the root. Some
    # py2exe/bbfreeze/non-CPython implementations don't do __file__, in which
    # case we can only use expanded keywords.

    keywords = { "refnames": git_refnames, "full": git_full }
    ver = git_versions_from_keywords(keywords, tag_prefix, verbose)
    if ver:
        return ver

    try:
        root = os.path.abspath(__file__)
        # versionfile_source is the relative path from the top of the source
        # tree (where the .git directory might live) to this file. Invert
        # this to find the root from __file__.
        for i in range(len(versionfile_source.split(os.sep))):
            root = os.path.dirname(root)
    except NameError:
        return default

    return (git_versions_from_vcs(tag_prefix, root, verbose)
            or versions_from_parentdir(parentdir_prefix, root, verbose)
            or default)
'''

def git_get_keywords(versionfile_abs):
    # the code embedded in _version.py can just fetch the value of these
    # keywords. When used from setup.py, we don't want to import _version.py,
    # so we do it with a regexp instead. This function is not used from
    # _version.py.
    keywords = {}
    try:
        f = open(versionfile_abs,"r")
        for line in f.readlines():
            if line.strip().startswith("git_refnames ="):
                mo = re.search(r'=\s*"(.*)"', line)
                if mo:
                    keywords["refnames"] = mo.group(1)
            if line.strip().startswith("git_full ="):
                mo = re.search(r'=\s*"(.*)"', line)
                if mo:
                    keywords["full"] = mo.group(1)
        f.close()
    except EnvironmentError:
        pass
    return keywords

def git_versions_from_keywords(keywords, tag_prefix, verbose=False):
    if not keywords:
        return {} # keyword-finding function failed to find keywords
    refnames = keywords["refnames"].strip()
    if refnames.startswith("$Format"):
        if verbose:
            print("keywords are unexpanded, not using")
        return {} # unexpanded, so not in an unpacked git-archive tarball
    refs = set([r.strip() for r in refnames.strip("()").split(",")])
    # starting in git-1.8.3, tags are listed as "tag: foo-1.0" instead of
    # just "foo-1.0". If we see a "tag: " prefix, prefer those.
    TAG = "tag: "
    tags = set([r[len(TAG):] for r in refs if r.startswith(TAG)])
    if not tags:
        # Either we're using git < 1.8.3, or there really are no tags. We use
        # a heuristic: assume all version tags have a digit. The old git %d
        # expansion behaves like git log --decorate=short and strips out the
        # refs/heads/ and refs/tags/ prefixes that would let us distinguish
        # between branches and tags. By ignoring refnames without digits, we
        # filter out many common branch names like "release" and
        # "stabilization", as well as "HEAD" and "master".
        tags = set([r for r in refs if re.search(r'\d', r)])
        if verbose:
            print("discarding '%s', no digits" % ",".join(refs-tags))
    if verbose:
        print("likely tags: %s" % ",".join(sorted(tags)))
    for ref in sorted(tags):
        # sorting will prefer e.g. "2.0" over "2.0rc1"
        if ref.startswith(tag_prefix):
            r = ref[len(tag_prefix):]
            if verbose:
                print("picking %s" % r)
            return { "version": r,
                     "full": keywords["full"].strip() }
    # no suitable tags, so we use the full revision id
    if verbose:
        print("no suitable tags, using full revision id")
    return { "version": keywords["full"].strip(),
             "full": keywords["full"].strip() }


def git_versions_from_vcs(tag_prefix, root, verbose=False):
    # this runs 'git' from the root of the source tree. This only gets called
    # if the git-archive 'subst' keywords were *not* expanded, and
    # _version.py hasn't already been rewritten with a short version string,
    # meaning we're inside a checked out source tree.

    if not os.path.exists(os.path.join(root, ".git")):
        if verbose:
            print("no .git in %s" % root)
        return {}

    GITS = ["git"]
    if sys.platform == "win32":
        GITS = ["git.cmd", "git.exe"]
    stdout = run_command(GITS, ["describe", "--tags", "--dirty", "--always"],
                         cwd=root)
    if stdout is None:
        return {}
    if not stdout.startswith(tag_prefix):
        if verbose:
            print("tag '%s' doesn't start with prefix '%s'" % (stdout, tag_prefix))
        return {}
    tag = stdout[len(tag_prefix):]
    stdout = run_command(GITS, ["rev-parse", "HEAD"], cwd=root)
    if stdout is None:
        return {}
    full = stdout.strip()
    if tag.endswith("-dirty"):
        full += "-dirty"
    return {"version": tag, "full": full}


def do_vcs_install(manifest_in, versionfile_source, ipy):
    GITS = ["git"]
    if sys.platform == "win32":
        GITS = ["git.cmd", "git.exe"]
    files = [manifest_in, versionfile_source]
    if ipy:
        files.append(ipy)
    try:
        me = __file__
        if me.endswith(".pyc") or me.endswith(".pyo"):
            me = os.path.splitext(me)[0] + ".py"
        versioneer_file = os.path.relpath(me)
    except NameError:
        versioneer_file = "versioneer.py"
    files.append(versioneer_file)
    present = False
    try:
        f = open(".gitattributes", "r")
        for line in f.readlines():
            if line.strip().startswith(versionfile_source):
                if "export-subst" in line.strip().split()[1:]:
                    present = True
        f.close()
    except EnvironmentError:
        pass
    if not present:
        f = open(".gitattributes", "a+")
        f.write("%s export-subst\n" % versionfile_source)
        f.close()
        files.append(".gitattributes")
    run_command(GITS, ["add", "--"] + files)

def versions_from_parentdir(parentdir_prefix, root, verbose=False):
    # Source tarballs conventionally unpack into a directory that includes
    # both the project name and a version string.
    dirname = os.path.basename(root)
    if not dirname.startswith(parentdir_prefix):
        if verbose:
            print("guessing rootdir is '%s', but '%s' doesn't start with prefix '%s'" %
                  (root, dirname, parentdir_prefix))
        return None
    return {"version": dirname[len(parentdir_prefix):], "full": ""}

SHORT_VERSION_PY = """
# This file was generated by 'versioneer.py' (0.12) from
# revision-control system data, or from the parent directory name of an
# unpacked source archive. Distribution tarballs contain a pre-generated copy
# of this file.

version_version = '%(version)s'
version_full = '%(full)s'
def get_versions(default={}, verbose=False):
    return {'version': version_version, 'full': version_full}

"""

DEFAULT = {"version": "unknown", "full": "unknown"}

def versions_from_file(filename):
    versions = {}
    try:
        with open(filename) as f:
            for line in f.readlines():
                mo = re.match("version_version = '([^']+)'", line)
                if mo:
                    versions["version"] = mo.group(1)
                mo = re.match("version_full = '([^']+)'", line)
                if mo:
                    versions["full"] = mo.group(1)
    except EnvironmentError:
        return {}

    return versions

def write_to_version_file(filename, versions):
    with open(filename, "w") as f:
        f.write(SHORT_VERSION_PY % versions)

    print("set %s to '%s'" % (filename, versions["version"]))


def get_root():
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.path.dirname(os.path.abspath(sys.argv[0]))

def vcs_function(vcs, suffix):
    return getattr(sys.modules[__name__], '%s_%s' % (vcs, suffix), None)

def get_versions(default=DEFAULT, verbose=False):
    # returns dict with two keys: 'version' and 'full'
    assert versionfile_source is not None, "please set versioneer.versionfile_source"
    assert tag_prefix is not None, "please set versioneer.tag_prefix"
    assert parentdir_prefix is not None, "please set versioneer.parentdir_prefix"
    assert VCS is not None, "please set versioneer.VCS"

    # I am in versioneer.py, which must live at the top of the source tree,
    # which we use to compute the root directory. py2exe/bbfreeze/non-CPython
    # don't have __file__, in which case we fall back to sys.argv[0] (which
    # ought to be the setup.py script). We prefer __file__ since that's more
    # robust in cases where setup.py was invoked in some weird way (e.g. pip)
    root = get_root()
    versionfile_abs = os.path.join(root, versionfile_source)

    # extract version from first of _version.py, VCS command (e.g. 'git
    # describe'), parentdir. This is meant to work for developers using a
    # source checkout, for users of a tarball created by 'setup.py sdist',
    # and for users of a tarball/zipball created by 'git archive' or github's
    # download-from-tag feature or the equivalent in other VCSes.

    get_keywords_f = vcs_function(VCS, "get_keywords")
    versions_from_keywords_f = vcs_function(VCS, "versions_from_keywords")
    if get_keywords_f and versions_from_keywords_f:
        vcs_keywords = get_keywords_f(versionfile_abs)
        ver = versions_from_keywords_f(vcs_keywords, tag_prefix)
        if ver:
            if verbose: print("got version from expanded keyword %s" % ver)
            return ver

    ver = versions_from_file(versionfile_abs)
    if ver:
        if verbose: print("got version from file %s %s" % (versionfile_abs,ver))
        return ver

    versions_from_vcs_f = vcs_function(VCS, "versions_from_vcs")
    if versions_from_vcs_f:
        ver = versions_from_vcs_f(tag_prefix, root, verbose)
        if ver:
            if verbose: print("got version from VCS %s" % ver)
            return ver

    ver = versions_from_parentdir(parentdir_prefix, root, verbose)
    if ver:
        if verbose: print("got version from parentdir %s" % ver)
        return ver

    if verbose: print("got version from default %s" % default)
    return default

def get_version(verbose=False):
    return get_versions(verbose=verbose)["version"]

class cmd_version(Command):
    description = "report generated version string"
    user_options = []
    boolean_options = []
    def initialize_options(self):
        pass
    def finalize_options(self):
        pass
    def run(self):
        ver = get_version(verbose=True)
        print("Version is currently: %s" % ver)


class cmd_build(_build):
    def run(self):
        versions = get_versions(verbose=True)
        _build.run(self)
        # now locate _version.py in the new build/ directory and replace it
        # with an updated value
        if versionfile_build:
            target_versionfile = os.path.join(self.build_lib, versionfile_build)
            print("UPDATING %s" % target_versionfile)
            os.unlink(target_versionfile)
            with open(target_versionfile, "w") as f:
                f.write(SHORT_VERSION_PY % versions)

if 'cx_Freeze' in sys.modules:  # cx_freeze enabled?
    from cx_Freeze.dist import build_exe as _build_exe

    class cmd_build_exe(_build_exe):
        def run(self):
            versions = get_versions(verbose=True)
            target_versionfile = versionfile_source
            print("UPDATING %s" % target_versionfile)
            os.unlink(target_versionfile)
            with open(target_versionfile, "w") as f:
                f.write(SHORT_VERSION_PY % versions)

            _build_exe.run(self)
            os.unlink(target_versionfile)
            with open(versionfile_source, "w") as f:
                assert VCS is not None, "please set versioneer.VCS"
                LONG = LONG_VERSION_PY[VCS]
                f.write(LONG % {"DOLLAR": "$",
                                "TAG_PREFIX": tag_prefix,
                                "PARENTDIR_PREFIX": parentdir_prefix,
                                "VERSIONFILE_SOURCE": versionfile_source,
                                })

class cmd_sdist(_sdist):
    def run(self):
        versions = get_versions(verbose=True)
        self._versioneer_generated_versions = versions
        # unless we update this, the command will keep using the old version
        self.distribution.metadata.version = versions["version"]
        return _sdist.run(self)

    def make_release_tree(self, base_dir, files):
        _sdist.make_release_tree(self, base_dir, files)
        # now locate _version.py in the new base_dir directory (remembering
        # that it may be a hardlink) and replace it with an updated value
        target_versionfile = os.path.join(base_dir, versionfile_source)
        print("UPDATING %s" % target_versionfile)
        os.unlink(target_versionfile)
        with open(target_versionfile, "w") as f:
            f.write(SHORT_VERSION_PY % self._versioneer_generated_versions)

INIT_PY_SNIPPET = """
from ._version import get_versions
__version__ = get_versions()['version']
del get_versions
"""

class cmd_update_files(Command):
    description = "install/upgrade Versioneer files: __init__.py SRC/_version.py"
    user_options = []
    boolean_options = []
    def initialize_options(self):
        pass
    def finalize_options(self):
        pass
    def run(self):
        print(" creating %s" % versionfile_source)
        with open(versionfile_source, "w") as f:
            assert VCS is not None, "please set versioneer.VCS"
            LONG = LONG_VERSION_PY[VCS]
            f.write(LONG % {"DOLLAR": "$",
                            "TAG_PREFIX": tag_prefix,
                            "PARENTDIR_PREFIX": parentdir_prefix,
                            "VERSIONFILE_SOURCE": versionfile_source,
                            })

        ipy = os.path.join(os.path.dirname(versionfile_source), "__init__.py")
        if os.path.exists(ipy):
            try:
                with open(ipy, "r") as f:
                    old = f.read()
            except EnvironmentError:
                old = ""
            if INIT_PY_SNIPPET not in old:
                print(" appending to %s" % ipy)
                with open(ipy, "a") as f:
                    f.write(INIT_PY_SNIPPET)
            else:
                print(" %s unmodified" % ipy)
        else:
            print(" %s doesn't exist, ok" % ipy)
            ipy = None

        # Make sure both the top-level "versioneer.py" and versionfile_source
        # (PKG/_version.py, used by runtime code) are in MANIFEST.in, so
        # they'll be copied into source distributions. Pip won't be able to
        # install the package without this.
        manifest_in = os.path.join(get_root(), "MANIFEST.in")
        simple_includes = set()
        try:
            with open(manifest_in, "r") as f:
                for line in f:
                    if line.startswith("include "):
                        for include in line.split()[1:]:
                            simple_includes.add(include)
        except EnvironmentError:
            pass
        # That doesn't cover everything MANIFEST.in can do
        # (http://docs.python.org/2/distutils/sourcedist.html#commands), so
        # it might give some false negatives. Appending redundant 'include'
        # lines is safe, though.
        if "versioneer.py" not in simple_includes:
            print(" appending 'versioneer.py' to MANIFEST.in")
            with open(manifest_in, "a") as f:
                f.write("include versioneer.py\n")
        else:
            print(" 'versioneer.py' already in MANIFEST.in")
        if versionfile_source not in simple_includes:
            print(" appending versionfile_source ('%s') to MANIFEST.in" %
                  versionfile_source)
            with open(manifest_in, "a") as f:
                f.write("include %s\n" % versionfile_source)
        else:
            print(" versionfile_source already in MANIFEST.in")

        # Make VCS-specific changes. For git, this means creating/changing
        # .gitattributes to mark _version.py for export-time keyword
        # substitution.
        do_vcs_install(manifest_in, versionfile_source, ipy)

def get_cmdclass():
    cmds = {'version': cmd_version,
            'versioneer': cmd_update_files,
            'build': cmd_build,
            'sdist': cmd_sdist,
            }
    if 'cx_Freeze' in sys.modules:  # cx_freeze enabled?
        cmds['build_exe'] = cmd_build_exe
        del cmds['build']

    return cmds
