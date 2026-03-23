#!/usr/bin/env python3
"""
Twitch bot that automatically joins giveaways across multiple channels.
"""

import asyncio
import argparse
import sys
from twitchio.ext import commands
from config import load_config
from bot import TwitchBot

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run Twitch bot runtime.')
    parser.add_argument(
        '--log-only',
        action='store_true',
        help='Run in logging-only mode (no giveaway detection and no trigger responses).',
    )
    return parser.parse_args()


async def main():
    bot = None
    twitch_bot = None
    args = parse_args()

    try:
        # Carrega configurações do JSON
        config = load_config()
        print('[📋] Configurações carregadas de configs/config.json')
        
        # Cria lista de canais para o bot
        channel_names = [ch.name for ch in config.channels]
        
        # Gera URL do multitwitch com canais ordenados alfabeticamente
        sorted_channels = sorted(channel_names)
        multitwitch_url = f"https://multitwitch.tv/{'/'.join(sorted_channels)}"
        print(f'[🔗] Multitwitch: {multitwitch_url}\n')
        
        # Cria a instância do bot
        bot = commands.Bot(
            token=config.twitch.oauth_token,
            nick=config.twitch.username,
            prefix='§',
            initial_channels=channel_names
        )
        
        # Adiciona a Cog
        twitch_bot = TwitchBot(bot, config, log_only_mode=args.log_only)
        bot.add_cog(twitch_bot)
        
        # Conecta à Twitch
        mode_label = 'LOG-ONLY' if args.log_only else 'FULL'
        print(f'[⚙️] Modo de execução: {mode_label}')
        print('[⏳] Conectando ao chat da Twitch...\n')
        await bot.start()
        
    except FileNotFoundError as e:
        print(f'[✗] Erro: {e}')
        sys.exit(1)
    except ValueError as e:
        print(f'[✗] Erro de configuração: {e}')
        sys.exit(1)
    except asyncio.CancelledError:
        if twitch_bot is not None:
            await twitch_bot.graceful_shutdown('task cancelled')
        raise
    except Exception as e:
        import traceback
        print(f'[✗] Erro: {e}')
        traceback.print_exc()
        sys.exit(1)
    finally:
        if twitch_bot is not None and not twitch_bot.is_shutting_down:
            await twitch_bot.graceful_shutdown('application shutdown')

if __name__ == '__main__':
    _exit_code = 0
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('[✓] Bot encerrado')
    except SystemExit as e:
        _exit_code = e.code if isinstance(e.code, int) else 1
    finally:
        if getattr(sys, 'frozen', False):
            input('\nPressione Enter para fechar...')
    sys.exit(_exit_code)
