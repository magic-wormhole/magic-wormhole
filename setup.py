import sys
from setuptools import setup

import versioneer

commands = versioneer.get_cmdclass()

DEV_REQUIREMENTS = [
    "mock",
    "tox",
    "pyflakes",
]
if sys.version_info[0] < 3:
    # txtorcon is not yet compatible with py3, so we include "txtorcon" in
    # DEV_REQUIREMENTS under py2 but not under py3. The test suite will skip
    # the tor tests when txtorcon is not importable. This results in
    # different wheels when built under py2 vs py3 (with different
    # extras_require[dev] dependencies), but I think this is ok, since nobody
    # should be installing with [dev] from a wheel.
    DEV_REQUIREMENTS.append("txtorcon")

setup(name="magic-wormhole",
      version=versioneer.get_version(),
      description="Securely transfer data between computers",
      author="Brian Warner",
      author_email="warner-magic-wormhole@lothar.com",
      license="MIT",
      url="https://github.com/warner/magic-wormhole",
      package_dir={"": "src"},
      packages=["wormhole",
                "wormhole.cli",
                "wormhole.server",
                "wormhole.test",
                ],
      package_data={"wormhole.server": ["db-schemas/*.sql"]},
      entry_points={
          "console_scripts":
          [
              "wormhole = wormhole.cli.cli:wormhole",
              "wormhole-server = wormhole.server.cli:server",
          ]
      },
      install_requires=[
          "spake2==0.7", "pynacl",
          "six",
          "twisted[tls]",
          "autobahn[twisted] >= 0.14.1",
          "hkdf", "tqdm",
          "click",
          "humanize",
          "ipaddress",
      ],
      extras_require={
          ':sys_platform=="win32"': ["pypiwin32"],
          "tor": ["txtorcon"],
          "dev": DEV_REQUIREMENTS, # includes txtorcon on py2, but not py3
      },
      test_suite="wormhole.test",
      cmdclass=commands,
      )
