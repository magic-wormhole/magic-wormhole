from setuptools import setup

import versioneer

commands = versioneer.get_cmdclass()

trove_classifiers = [
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: Implementation :: CPython",
    "Topic :: Security :: Cryptography",
    "Topic :: System :: Networking",
    "Topic :: System :: Systems Administration",
    "Topic :: Utilities",
    ]

setup(name="magic-wormhole",
      version=versioneer.get_version(),
      description="Securely transfer data between computers",
      long_description=open('README.md').read(),
      long_description_content_type='text/markdown',
      author="Brian Warner",
      author_email="warner-magic-wormhole@lothar.com",
      license="MIT",
      url="https://github.com/warner/magic-wormhole",
      classifiers=trove_classifiers,
      python_requires=">=3.10",

      package_dir={"": "src"},
      packages=["wormhole",
                "wormhole.cli",
                "wormhole._dilation",
                "wormhole.test",
                "wormhole.test.dilate",
                ],
      data_files=[(".", ["wormhole_complete.bash", "wormhole_complete.zsh", "wormhole_complete.fish"])],
      entry_points={
          "console_scripts":
          [
              "wormhole = wormhole.cli.cli:wormhole",
              "magic-wormhole = wormhole.cli.cli:wormhole", # it's advantageous to have an entry point that matches the package name, for things like `uv tool run`
          ]
      },
      install_requires=[
          "spake2==0.9", "pynacl",
          "attrs >= 19.2.0", # 19.2.0 replaces cmp parameter with eq/order
          "twisted[tls] >= 17.5.0", # 17.5.0 adds failAfterFailures=
          "autobahn[twisted] >= 0.14.1, != 25.9.1, != 25.10.1",
          "automat",
          "cryptography",
          "tqdm >= 4.13.0", # 4.13.0 fixes crash on NetBSD
          "click",
          "humanize",
          "txtorcon >= 18.0.2", # 18.0.2 fixes py3.4 support
          "zipstream-ng >= 1.7.1, <2.0.0",
          "iterable-io >= 1.0.0, <2.0.0",
          "qrcode >= 8.0",
      ],
      extras_require={
          ':sys_platform=="win32"': ["pywin32"],
          "dev": [
              "tox",
              "pyflakes",
              "magic-wormhole-transit-relay",
              "magic-wormhole-mailbox-server",
              "pytest",
              "pytest_twisted",
              "hypothesis",
          ],
          "dilate": ["noiseprotocol"],
          "build": ["twine", "dulwich", "readme_renderer", "pysequoia", "wheel"],
      },
      test_suite="wormhole.test",
      cmdclass=commands,
      )
