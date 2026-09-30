"""Run as an always-on process: python -m smartplate.reminder_worker [--once]."""
import argparse
import time

from . import config, push
from .runtime import initialize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    initialize()
    while True:
        push.send_due()
        if args.once:
            return
        time.sleep(config.PUSH_TICK_S)


if __name__ == '__main__':
    main()
