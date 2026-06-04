#!/usr/bin/env python3
"""
Twitch bot that automatically joins giveaways across multiple channels using multiple accounts.
"""

import asyncio
import argparse
import sys
from twitchio.ext import commands
from config import load_config
from bot import TwitchBot
from console_log import log_line
from startup_logs import emit_startup_logs

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run Twitch bot runtime.')
    parser.add_argument(
        '--log',
        action='store_true',
        help='Enable file logging to logs/ while keeping normal bot behavior.',
    )
    parser.add_argument(
        '--log-only',
        action='store_true',
        help='Run in logging-only mode (no giveaway detection and no trigger responses).',
    )
    parser.add_argument(
        '--gui',
        action='store_true',
        default=True,
        help='Launch the Textual TUI monitor (default when no flags are given).',
    )
    parser.add_argument(
        '--no-gui',
        action='store_true',
        help='Disable the TUI and use plain terminal output.',
    )
    return parser.parse_args()


async def run_bot_account(account, config, args):
    """Run a single bot instance for one account."""
    bot = commands.Bot(
        token=account.oauth_token,
        nick=account.username,
        prefix='§',
        initial_channels=[ch.name for ch in config.channels]
    )
    
    twitch_bot = TwitchBot(
        bot,
        config,
        account_name=account.username,
        account_nickname=account.nickname,
        ignored_usernames=account.ignored_usernames,
        log_only_mode=args.log_only,
        enable_logging=(args.log or args.log_only),
    )
    bot.add_cog(twitch_bot)
    await bot.start()


async def main():
    args = parse_args()

    try:
        config = load_config()
        emit_startup_logs(config, args)
        print()
        print()

        bot_tasks = []
        for account in config.accounts:
            task = run_bot_account(account, config, args)
            bot_tasks.append(task)

        await asyncio.gather(*bot_tasks, return_exceptions=False)
    except FileNotFoundError as e:
        log_line(f'Erro: {e}', 'other')
        sys.exit(1)
    except ValueError as e:
        log_line(f'Erro de configuracao: {e}', 'other')
        sys.exit(1)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        import traceback
        log_line(f'Erro: {e}', 'other')
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    _args = parse_args()
    if _args.no_gui:
        _args.gui = False
    if _args.gui:
        try:
            from monitor_gui import run_gui
            _config = load_config()
            run_gui(_config, _args)
        except ImportError as e:
            log_line(f'GUI dependency missing: {e}', 'ignore')
            log_line('Run: pip install textual rich', 'ignore')
            import traceback
            traceback.print_exc()
            input('Press Enter to exit...')
            sys.exit(1)
        except Exception as e:
            log_line(f'GUI error: {e}', 'ignore')
            import traceback
            traceback.print_exc()
            input('Press Enter to exit...')
            sys.exit(1)
        sys.exit(0)

    _exit_code = 0
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log_line('Bot encerrado', 'other')
    except SystemExit as e:
        _exit_code = e.code if isinstance(e.code, int) else 1
    finally:
        if getattr(sys, 'frozen', False):
            input('\nPressione Enter para fechar...')
    sys.exit(_exit_code)
