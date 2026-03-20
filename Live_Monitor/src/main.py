#!/usr/bin/env python3
"""
Twitch bot that automatically joins giveaways across multiple channels.
"""

import asyncio
import sys
from twitchio.ext import commands
from config import load_config
from bot import TwitchBot

async def main():
    bot = None
    twitch_bot = None

    try:
        # Carrega configurações do JSON
        config = load_config()
        print('[📋] Configurações carregadas de configs/config.json')
        
        # Cria lista de canais para o bot
        channel_names = [ch.name for ch in config.channels]
        
        # Cria a instância do bot
        bot = commands.Bot(
            token=config.twitch.oauth_token,
            nick=config.twitch.username,
            prefix='§',
            initial_channels=channel_names
        )
        
        # Adiciona a Cog
        twitch_bot = TwitchBot(bot, config)
        bot.add_cog(twitch_bot)
        
        # Conecta à Twitch
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
        print(f'[✗] Erro: {e}')
        sys.exit(1)
    finally:
        if twitch_bot is not None and not twitch_bot.is_shutting_down:
            await twitch_bot.graceful_shutdown('application shutdown')

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('[✓] Bot encerrado')
