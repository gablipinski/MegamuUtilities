import sys
import argparse

from app_version import APP_VERSION
from monitor_ui import MonitorUI


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run Watchtower monitor.')
    parser.add_argument(
        '--version',
        action='version',
        version=f'%(prog)s {APP_VERSION}',
    )
    return parser.parse_args()


def main():
    parse_args()
    ui = MonitorUI()
    ui.run()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('[INFO] Monitoring stopped')
    except Exception as exc:
        print(f'[ERROR] Error: {exc}')
        sys.exit(1)
