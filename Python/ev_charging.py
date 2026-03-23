"""Compatibility entrypoint for users expecting `ev_charging.py`."""

import sys

from runtime_bootstrap import bootstrap_runtime

bootstrap_runtime(
    script_file=__file__,
    argv=sys.argv,
    required_modules=('numpy', 'matplotlib'),
    is_main=__name__ == '__main__',
)

from ev_charging_sim import main


if __name__ == '__main__':
    main()
