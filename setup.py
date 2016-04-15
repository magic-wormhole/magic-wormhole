
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
                "wormhole.blocking", "wormhole.twisted",
                "wormhole.scripts", "wormhole.test", "wormhole.util",
                "wormhole_server"],
      package_data={"wormhole": ["db-schemas/*.sql"]},
      entry_points={"console_scripts":
                    ["wormhole = wormhole.scripts.runner:entry",
                     "wormhole-server = wormhole_server.runner:entry",
                     ]},
      install_requires=["spake2==0.3", "pynacl", "requests", "argparse",
                        "six", "twisted >= 16.1.0"],
      extras_require={"tor": ["txtorcon", "ipaddr"]},
      test_suite="wormhole.test",
      cmdclass=commands,
      )
