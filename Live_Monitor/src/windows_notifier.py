from config import NotificationConfig

try:
    from winotify import Notification
except Exception:
    Notification = None

class WindowsNotifier:
    """Sends Windows toast notifications"""
    
    def __init__(self, config: NotificationConfig):
        self.config = config
        self._warned = False
    
    def send_notification(self, channel: str, message: str, title: str = '🎰 Giveaway detected!') -> bool:
        """Sends a Windows toast notification"""
        
        if not self.config.enabled:
            return False

        if Notification is None:
            if not self._warned:
                print('[!] Windows notifications unavailable (winotify missing).')
                self._warned = True
            return False
        
        body = self.config.message.format(
            channel=channel,
            message=message[:50] + '...' if len(message) > 50 else message
        )
        
        try:
            toast = Notification(
                app_id='Twitch Bot',
                title=title,
                msg=body,
            )
            toast.show()
            print(f'[🔔] Windows notification sent: {body}')
            return True
        except Exception as e:
            print(f'[✗] Error sending notification: {e}')
            return False
