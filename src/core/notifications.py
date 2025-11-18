import telegram
from telegram.constants import ParseMode
from typing import Optional
from core.logger import ArbitriumLogger

logger = ArbitriumLogger('notification_manager', 'arbitrium.log')

class NotificationManager:
    def __init__(self, token: Optional[str], chat_id: Optional[str]):
        """
        Inicializa el gestor de notificaciones.

        Args:
            token: El token del Bot de Telegram.
            chat_id: El ID del chat de Telegram a donde se enviarán los mensajes.
        """
        if token and chat_id:
            self.bot = telegram.Bot(token=token)
            self.chat_id = chat_id
            self.enabled = True
            logger.info("NotificationManager inicializado para Telegram.")
        else:
            self.bot = None
            self.chat_id = None
            self.enabled = False
            logger.warning("NotificationManager deshabilitado. No se proporcionó token o chat_id de Telegram.")

    async def send_message(self, message: str, is_html: bool = True):
        """
        Envía un mensaje a través del bot de Telegram de forma asíncrona.

        Args:
            message: El contenido del mensaje a enviar.
            is_html: Si el mensaje debe ser parseado como HTML.
        """
        #Se añade la comprobación de self.chat_id para ser más explícitos
        if not self.enabled or not self.bot or not self.chat_id:
            return

        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=ParseMode.HTML if is_html else None
            )
            logger.debug(f"Mensaje de notificación enviado: {message[:50]}...")
        except telegram.error.TelegramError as e:
            logger.error(f"Error al enviar notificación de Telegram: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"Error inesperado en NotificationManager: {e}", exc_info=True)

    async def send_trade_success_notification(self, trade_data: dict):
        """Formatea y envía una notificación para una transacción exitosa."""
        route_str = trade_data.get('route', 'N/A').replace(" → ", " ➡️ ")
        tx_hash = trade_data.get('tx_hash', 'N/A')
        net_profit = trade_data.get('net_profit_usdt')
        
        profit_str = f"+${net_profit:.4f}" if net_profit is not None else "N/A"
        
        message = (
            f"✅ <b>¡Arbitraje Exitoso!</b> ✅\n\n"
            f"<b>Ruta:</b> {route_str}\n"
            f"<b>Ganancia Neta (USDT):</b> <code>{profit_str}</code>\n"
            f"<b>Hash:</b> <a href='https://bscscan.com/tx/{tx_hash}'>{tx_hash[:10]}...</a>"
        )
        await self.send_message(message)

    async def send_trade_failure_notification(self, trade_data: dict):
        """Formatea y envía una notificación para una transacción fallida."""
        route_str = trade_data.get('route', 'N/A').replace(" → ", " ➡️ ")
        status = trade_data.get('status', 'failed')
        tx_hash = trade_data.get('tx_hash')
        
        message = (
            f"❌ <b>¡Fallo en Arbitraje!</b> ❌\n\n"
            f"<b>Ruta:</b> {route_str}\n"
            f"<b>Estado:</b> <code>{status}</code>\n"
        )
        if tx_hash:
            message += f"<b>Hash:</b> <a href='https://bscscan.com/tx/{tx_hash}'>{tx_hash[:10]}...</a>"

        await self.send_message(message)