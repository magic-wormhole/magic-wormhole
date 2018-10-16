from setuptools import setup

import versioneer

commands = versioneer.get_cmdclass()

trove_classifiers = [
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "License :: OSI Approved :: MIT License",
    "Topic :: Security :: Cryptography",
    "Topic :: System :: Networking",
    "Topic :: System :: Systems Administration",
    "Topic :: Utilities",
    ]

setup(name="magic-wormhole",
      version=versioneer.get_version(),
      description="Securely transfer data between computers",
      long_description=open('README.md', 'rU').read(),
      long_description_content_type='text/markdown',
      author="Brian Warner",
      author_email="warner-magic-wormhole@lothar.com",
      license="MIT",
      url="https://github.com/warner/magic-wormhole",
      classifiers=trove_classifiers,

      package_dir={"": "src"},
      packages=["wormhole",
                "wormhole.cli",
                "wormhole.test",
                ],
      entry_points={
          "console_scripts":
          [
              "wormhole = wormhole.cli.cli:wormhole",
          ]
      },
      install_requires=[
          "spake2==0.8", "pynacl",
          "six",
          "attrs >= 16.3.0", # 16.3.0 adds __attrs_post_init__
          "twisted[tls] >= 17.5.0", # 17.5.0 adds failAfterFailures=
          "autobahn[twisted] >= 0.14.1",
          "automat",
          "hkdf",
          "tqdm >= 4.13.0", # 4.13.0 fixes crash on NetBSD
          "click",
          "humanize",
          "txtorcon >= 18.0.2", # 18.0.2 fixes py3.4 support
      ],
      extras_require={
          ':sys_platform=="win32"': ["pywin32"],
          "dev": ["mock", "tox", "pyflakes",
                  "magic-wormhole-transit-relay==0.1.2",
                  "magic-wormhole-mailbox-server==0.3.1"],
      },
      test_suite="wormhole.test",
      cmdclass=commands,
      )
