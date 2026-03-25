from __future__ import annotations

import argparse

from config import BotConfig
from console_log import log_line


def emit_startup_logs(config: BotConfig, args: argparse.Namespace) -> None:
    log_line('Configuracoes carregadas de configs/config.json', 'other')

    channel_names = [channel.name for channel in config.channels]
    sorted_channels = sorted(channel_names)
    multitwitch_url = f"https://multitwitch.tv/{'/'.join(sorted_channels)}"

    log_line(f'Multitwitch: {multitwitch_url}', 'other')
    log_line(f'Contas: {", ".join(account.username for account in config.accounts)}', 'other')

    if args.log_only:
        mode_label = 'LOG-ONLY'
    else:
        mode_label = 'FULL'

    log_line(f'Modo de execucao: {mode_label}', 'other')
    log_line(f'Iniciando {len(config.accounts)} conta(s)...', 'other')
