import sys

from monitor_ui import MonitorUI


def main():
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
