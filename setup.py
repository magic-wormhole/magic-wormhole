
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
                "wormhole.twisted",
                ],
      package_data={"wormhole.server": ["db-schemas/*.sql"]},
      entry_points={"console_scripts":
                    ["wormhole = wormhole.cli.runner:entry",
                     "wormhole-server = wormhole.server.runner:entry",
                     ]},
      install_requires=["spake2==0.3", "pynacl", "argparse",
                        "six",
                        "twisted==16.1.1", # since autobahn pins it
                        "autobahn[twisted]",
                        "hkdf", "tqdm",
                        ],
      extras_require={"tor": ["txtorcon", "ipaddr"]},
      test_suite="wormhole.test",
      cmdclass=commands,
      )
