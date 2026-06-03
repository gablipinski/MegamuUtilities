import sys

from monitor_ui import MonitorUI


def main():
    ui = MonitorUI()
    ui.run()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('[✓] Monitoramento encerrado')
    except Exception as exc:
        print(f'[✗] Erro: {exc}')
        sys.exit(1)
