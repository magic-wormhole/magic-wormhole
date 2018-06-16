# -*- mode: python -*-
# this pyinstaller spec file is used to build wormhole binaries on posix platforms

import os, sys

# your cwd should be in the same dir as this file, so .. is the project directory:
basepath = os.path.realpath('..')

a = Analysis([os.path.join(basepath, 'src/wormhole/__main__.py'), ],
             pathex=[basepath, ],
             binaries=[],
             datas=[],
             hiddenimports=[],
             hookspath=[],
             runtime_hooks=[],
             excludes=[],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=None)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(pyz,
          a.scripts,
          a.binaries,
          a.zipfiles,
          a.datas,
          name='wormhole.exe',
          debug=False,
          strip=False,
          upx=True,
          console=True)

if False:
    # Enable this block to build a directory-based binary instead of
    # a packed single file.
    coll = COLLECT(exe,
                   a.binaries,
                   a.zipfiles,
                   a.datas,
                   strip=False,
                   upx=True,
                   name='wormhole-dir')
