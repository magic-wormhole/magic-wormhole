
from setuptools import setup

import versioneer

commands = versioneer.get_cmdclass()

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
      ],
      extras_require={
          ':sys_platform=="win32"': ["pypiwin32"],
          "tor": ["txtorcon", "ipaddress"],
          "dev": [
              "mock",
              "tox",
              "pyflakes",
          ],
      },
      test_suite="wormhole.test",
      cmdclass=commands,
      )
