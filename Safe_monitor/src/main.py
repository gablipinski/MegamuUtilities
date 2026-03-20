#!/usr/bin/env python3
"""
Safe Monitor - Utilidade de monitoramento de tela para MU Online
Detecta personagens em múltiplas janelas de jogo e notifica via WhatsApp
"""

import asyncio
import sys
from config import load_config
from area_selector import build_windows_interactively
from screen_monitor import ScreenMonitor
from whatsapp_notifier import WhatsAppNotifier

async def main():
    monitor = None
    
    try:
        # Carrega configurações
        config = load_config()
        print('[📋] Configurações base carregadas de configs/config.json')

        print('\n[🛠️] Configuração interativa de regiões')
        print('Selecione uma região, informe o mapa e repita até iniciar o scan.')
        selected_windows = build_windows_interactively()

        if not selected_windows:
            raise ValueError('Nenhuma região selecionada para monitoramento')

        # Cada região selecionada já é o recorte final de OCR.
        config.windows = selected_windows
        config.scan_region = {
            'left_pct': 0.0,
            'top_pct': 0.0,
            'right_pct': 1.0,
            'bottom_pct': 1.0,
        }

        print(f'\n[📺] Monitorando {len(config.windows)} região(ões):')
        for window in config.windows:
            print(f'    - {window.position}: {window.map_name} ({window.x}, {window.y}, {window.width}x{window.height})')
        
        # Inicializa notificador
        notifier = WhatsAppNotifier(config.notification)
        if config.notification.enabled:
            print(f'[✓] Notificações WhatsApp habilitadas')
        else:
            print(f'[⚠️] Notificações WhatsApp desabilitadas')
        
        # Cria monitor
        monitor = ScreenMonitor(config)
        
        # Inicia monitoramento
        print('[⏳] Iniciando...\n')
        await monitor.start_monitoring()
        
    except FileNotFoundError as e:
        print(f'[✗] Erro: {e}')
        sys.exit(1)
    except ValueError as e:
        print(f'[✗] Erro de configuração: {e}')
        sys.exit(1)
    except Exception as e:
        print(f'[✗] Erro: {e}')
        sys.exit(1)
    finally:
        if monitor is not None:
            await monitor.stop_monitoring()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('[✓] Monitoramento encerrado')
