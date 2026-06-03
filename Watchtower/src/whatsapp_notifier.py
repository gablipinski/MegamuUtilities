from config import NotificationConfig

class WhatsAppNotifier:
    """Sends notifications to WhatsApp."""
    
    def __init__(self, config: NotificationConfig):
        self.config = config
        
        if config.enabled and not config.whatsapp_group_id:
            print('[WARN] WhatsApp enabled but group_id is not configured')
    
    async def send_notification(self, char_name: str, map_name: str) -> bool:
        """
        Sends a notification to the WhatsApp group.

        For real Twilio integration, you need to:
        1. pip install twilio
        2. Configure Twilio credentials in config.json
        3. Uncomment the code below
        """
        
        if not self.config.enabled:
            return False
        
        notification = self.config.notification_message.format(
            char_name=char_name,
            map=map_name
        )
        
        print(f'[INFO] WhatsApp: {notification}')
        
        # TODO: Implement real Twilio integration.
        # Example Twilio integration:
        # from twilio.rest import Client
        # client = Client(account_sid, auth_token)
        # client.messages.create(
        #     body=notification,
        #     from_='whatsapp:+1234567890',
        #     to=f'whatsapp:{self.config.whatsapp_group_id}'
        # )
        
        return True
