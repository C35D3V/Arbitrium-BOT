import sys
import os
import asyncio
import argparse
from typing import Optional

# Añade el directorio 'src' a la ruta del sistema
project_root = os.path.dirname(os.path.abspath(__file__))
src_path = os.path.join(project_root, 'src')
sys.path.insert(0, src_path)

from dotenv import load_dotenv
from rich.console import Console
from rich.live import Live

from core.logger import ArbitriumLogger
from bsc.data_fetcher import BSCDataFetcher
from bsc.arbitrage import CombinedArbitrage
from bsc.pool_monitor import PoolMonitor
from bsc.execution import BSCTransactionExecutor
from core.interface import BotInterface
from core.database import DatabaseManager
from core.risk_management import RiskManager
from core.settings_manager import SettingsManager
from core.notifications import NotificationManager

logger = ArbitriumLogger('main', 'arbitrium.log')
console = Console()

class ArbitriumBot:
    def __init__(self):
        self.running = False
        self.db_manager = None
        self.settings_manager: Optional[SettingsManager] = None

        try:
            load_dotenv()
            logger.info("Archivo .env cargado.")

            self.settings_manager = SettingsManager()
            logger.info("SettingsManager inicializado y configuración cargada.")

            telegram_token = os.getenv("TELEGRAM_BOT_TOKEN") or self.settings_manager.get_setting("TELEGRAM_BOT_TOKEN")
            telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID") or self.settings_manager.get_setting("TELEGRAM_CHAT_ID")

            self.notification_manager = NotificationManager(
                token=telegram_token,
                chat_id=telegram_chat_id
            )

            self.db_manager = DatabaseManager()
            self.interface = BotInterface(db_manager=self.db_manager, settings_manager=self.settings_manager)

            self.fetcher = BSCDataFetcher()

            private_key = os.getenv("PRIVATE_KEY")
            fork_rpc_url = os.getenv("FORK_RPC_URL")

            if not private_key:
                raise ValueError("La variable de entorno PRIVATE_KEY no se encontró en el archivo .env")

            self.executor = BSCTransactionExecutor(
                w3=self.fetcher.w3,
                private_key=private_key,
                db_manager=self.db_manager,
                fork_rpc_url=fork_rpc_url,
                data_fetcher=self.fetcher,
                settings_manager=self.settings_manager,
                notification_manager=self.notification_manager
            )

            self.risk_manager = RiskManager(
                fetcher=self.fetcher,
                executor=self.executor,
                settings_manager=self.settings_manager
            )

            self.pool_monitor: Optional[PoolMonitor] = None
            self.arbitrage_engine: Optional[CombinedArbitrage] = None

        except (SystemExit, ValueError, ConnectionError) as e:
            console.print(f"[bold red]❌ Error fatal al inicializar el bot: {e}[/bold red]")
            console.print("[bold red]   Revisa 'arbitrium.log' y tu archivo '.env'.[/bold red]")
            if self.db_manager:
                self.db_manager.close()
            sys.exit(1)

    async def initialize_arbitrage_engine(self):
        if not self.fetcher.wss_url:
            logger.error("No se puede inicializar el motor de arbitraje sin una conexión WebSocket (WSS_URL).")
            return

        if not self.db_manager or not self.settings_manager:
            logger.critical("DatabaseManager o SettingsManager no fueron inicializados. Abortando.")
            return

        logger.info("Inicializando motor de arbitraje combinado...")

        self.arbitrage_engine = CombinedArbitrage(
            fetcher=self.fetcher,
            interface=self.interface,
            db_manager=self.db_manager,
            executor=self.executor,
            risk_manager=self.risk_manager,
            settings_manager=self.settings_manager
        )

        event_callback = self.arbitrage_engine.multi_step_arbitrage.on_pool_update

        self.pool_monitor = PoolMonitor.create(
            data_fetcher=self.fetcher,
            db_manager=self.db_manager,
            event_callback=event_callback
        )

        if self.pool_monitor and self.arbitrage_engine:
            await self.arbitrage_engine.initialize(self.pool_monitor)
            logger.info("Motor de arbitraje combinado inicializado y enlazado correctamente.")
        else:
            logger.error("Fallo al inicializar los componentes de arbitraje.")

    async def start(self, task: Optional[str] = None):
        self.running = True
        await self.initialize_arbitrage_engine()

        if self.pool_monitor:
            self.pool_monitor.start()

        if not self.settings_manager:
            logger.critical("SettingsManager no está disponible. Abortando.")
            self.running = False
            return
            
        # --- LÓGICA DE INICIO ---
        if task == "monitor":
            console.print("[bold cyan]Iniciando en modo de monitoreo no interactivo...[/bold cyan]")
            await self.monitor_combined_arbitrage()
        else:
            # Si no se especifica una tarea, se ejecuta el menú interactivo
            while self.running:
                self.interface.show_main_menu()
                choice = self._get_user_choice()

                if choice == "1":
                    await self.monitor_prices()
                elif choice == "2":
                    await self.monitor_combined_arbitrage()
                elif choice == "3":
                    self.interface.show_transaction_history()
                    # Usamos input() para pausar la ejecución y esperar al usuario
                    console.input("\n[bold green]Presiona Enter para volver al menú...[/bold green]")
                elif choice == "4":
                    if self.settings_manager:
                        self.interface.show_configuration_menu()
                    else:
                        self.interface.display_message("\n[red]Error: SettingsManager no está inicializado.[/red]")
                        await asyncio.sleep(2)
                elif choice == "5":
                    logger.info("Saliendo del bot...")
                    self.running = False
                else:
                    self.interface.display_message("\n[red]Opción no válida.[/red]")
                    await asyncio.sleep(2)

        if self.pool_monitor:
            self.pool_monitor.stop()

        console.print("[bold green]👋 ¡Adiós! El bot se ha cerrado correctamente.[/bold green]")

    async def monitor_prices(self):
        logger.info("Iniciando monitoreo de precios en tiempo real. Presiona 'Ctrl+C' para volver.")
        if not self.settings_manager:
            logger.error("SettingsManager no está disponible.")
            self.interface.display_message("[red]Error interno: configuración de precios no disponible.[/red]")
            return
        
        update_interval = self.settings_manager.get_setting("PRICE_UPDATE_INTERVAL_SECONDS", 10)
        
        try:
            with Live(self.interface.layout, screen=True, redirect_stderr=False, refresh_per_second=4) as live:
                self.interface.update_footer_status("Iniciando monitoreo...")
                while True:
                    try:
                        self.interface.update_footer_status("Obteniendo precios desde API...")
                        current_prices = await self.fetcher.get_current_prices()

                        if current_prices:
                            self.interface.update_price_data(current_prices)
                            self.interface.update_footer_status(f"Mostrando {len(current_prices)} precios. Esperando...")
                        else:
                            self.interface.update_footer_status("Advertencia: No se pudieron obtener precios.")

                    except Exception as e:
                        logger.error(f"Error al obtener precios: {e}", exc_info=True)
                        self.interface.update_footer_status(f"Error: {e}")
                    
                    await asyncio.sleep(update_interval)
        except KeyboardInterrupt:
            logger.info("Saliendo del monitoreo de precios.")
            return

    async def monitor_combined_arbitrage(self):
        logger.info("Iniciando monitoreo de Arbitraje Combinado (Reactivo + Activo).")
        self.interface.show_submenu("Arbitraje Combinado")

        if not self.pool_monitor or not self.arbitrage_engine:
            self.interface.display_message("\n[red]El motor de arbitraje no está activo. Revisa la conexión WSS y la PRIVATE_KEY.[/red]")
            await asyncio.sleep(3)
            return
            
        if not self.settings_manager:
            logger.error("SettingsManager no está disponible.")
            self.interface.display_message("[red]Error interno: configuración de arbitraje no disponible.[/red]")
            await asyncio.sleep(2)
            return
            
        arb_check_interval = self.settings_manager.get_setting("ARB_CHECK_INTERVAL_SECONDS", 15)

        try:
            with Live(self.interface.layout, screen=True, redirect_stderr=False, refresh_per_second=2) as live:
                self.interface.update_footer_status("Escuchando eventos y buscando activamente...")

                while True:
                    cross_dex_opps = await self.arbitrage_engine.find_opportunities()
                    long_path_opps = await self.arbitrage_engine.multi_step_arbitrage.find_long_path_opportunities()

                    all_opps = list(cross_dex_opps) + long_path_opps

                    if all_opps:
                        all_opps.sort(key=lambda opp: opp.get('profit_usdt', opp.get('profit', 0)), reverse=True)
                        self.interface.update_opportunities_panel(all_opps)
                        self.interface.update_footer_status(f"Detectadas {len(all_opps)} oportunidades.")
                    else:
                        self.interface.update_opportunities_panel([])
                        self.interface.update_footer_status("No se encontraron nuevas oportunidades. Esperando...")

                    await asyncio.sleep(arb_check_interval)

        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Saliendo del monitoreo de Arbitraje Combinado.")

    def _get_user_choice(self) -> str:
        return console.input("\n[bold green]Selecciona una opción > [/bold green]").strip()

if __name__ == "__main__":
    # --- LÓGICA DE PARSEO DE ARGUMENTOS ---
    parser = argparse.ArgumentParser(description="Arbitrium BOT - Bot de Arbitraje de Criptomonedas.")
    parser.add_argument("--task", type=str, choices=['monitor'], help="Inicia una tarea específica sin el menú interactivo. Opciones: 'monitor'")
    args = parser.parse_args()
    
    bot = None
    try:
        bot = ArbitriumBot()
        asyncio.run(bot.start(task=args.task)) 
    except KeyboardInterrupt:
        console.print("\n[bold red]🛑 Salida forzada del bot.[/bold red]")
    except Exception as e:
        logger.critical(f"Error crítico en main: {str(e)}", exc_info=True)
        console.print(f"[red]❌ ¡Un error crítico ha ocurrido! Consulta 'arbitrium.log' para más detalles.[/red]")
        sys.exit(1)
    finally:
        if bot and bot.db_manager:
            bot.db_manager.close()