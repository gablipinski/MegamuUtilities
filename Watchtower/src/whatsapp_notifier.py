from config import NotificationConfig

class WhatsAppNotifier:
    """Envia notificações para WhatsApp"""
    
    def __init__(self, config: NotificationConfig):
        self.config = config
        
        if config.enabled and not config.whatsapp_group_id:
            print('[⚠️] WhatsApp habilitado mas sem group_id configurado')
    
    async def send_notification(self, char_name: str, map_name: str) -> bool:
        """
        Envia notificação para o grupo WhatsApp
        
        Para integração real com Twilio, você precisa:
        1. pip install twilio
        2. Configurar credenciais da Twilio em config.json
        3. Descomentar o código abaixo
        """
        
        if not self.config.enabled:
            return False
        
        notification = self.config.notification_message.format(
            char_name=char_name,
            map=map_name
        )
        
        print(f'[📱] WhatsApp: {notification}')
        
        # TODO: Implementar integração real com Twilio
        # Exemplo de integração com Twilio:
        # from twilio.rest import Client
        # client = Client(account_sid, auth_token)
        # client.messages.create(
        #     body=notification,
        #     from_='whatsapp:+1234567890',
        #     to=f'whatsapp:{self.config.whatsapp_group_id}'
        # )
        
        return True
