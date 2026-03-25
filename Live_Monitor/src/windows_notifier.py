from config import NotificationConfig
from console_log import log_line

try:
    from winotify import Notification
except Exception:
    Notification = None

class WindowsNotifier:
    """Sends Windows toast notifications"""
    
    def __init__(self, config: NotificationConfig):
        self.config = config
        self._warned = False
    
    def send_notification(
        self,
        channel: str,
        message: str,
        title: str = 'Giveaway detected!',
        account: str | None = None,
    ) -> bool:
        """Sends a Windows toast notification"""
        
        if not self.config.enabled:
            return False

        if Notification is None:
            if not self._warned:
                log_line(
                    'Windows notifications unavailable (winotify missing).',
                    'notification',
                    channel,
                    account=account,
                )
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
            log_line(f'Windows notification sent: {body}', 'decision', channel, account=account)
            return True
        except Exception as e:
            log_line(f'Error sending notification: {e}', 'notification', channel, account=account)
            return False
