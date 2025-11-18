from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.layout import Layout
from rich.text import Text
from typing import Dict, List, Any, Sequence, Mapping, Optional, TYPE_CHECKING
from datetime import datetime
import time
import os

if TYPE_CHECKING:
    from core.database import DatabaseManager
    from core.settings_manager import SettingsManager

console = Console()

class BotInterface:
    def __init__(self, db_manager: Optional["DatabaseManager"] = None, settings_manager: Optional["SettingsManager"] = None):
        self.layout = Layout()
        self.db_manager = db_manager
        self.settings_manager = settings_manager
        self._setup_layout()

    def _setup_layout(self):
        self.layout.split(
            Layout(name="header", size=3),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=3)
        )
        self.layout["header"].update(Panel("[bold magenta]🤖 Arbitrium BOT 🤖[/bold magenta]", style="green", title="Principal"))
        self.layout["footer"].update(self._make_footer_panel("Listo"))

    def _make_footer_panel(self, status: str) -> Panel:
        footer_table = Table.grid(expand=True)
        footer_table.add_column(justify="left", no_wrap=True)
        footer_table.add_column(justify="right", no_wrap=True)
        now = datetime.now().strftime("%H:%M:%S")
        footer_table.add_row(
            "[yellow]Presiona [bold]Ctrl+C[/bold] para volver[/yellow]",
            f"[dim]({now}) {status}[/dim]"
        )
        return Panel(footer_table, title="Estado", border_style="blue")

    def update_footer_status(self, message: str):
        self.layout["footer"].update(self._make_footer_panel(message))

    def display_message(self, message: str, style: str = "info"):
        console.print(f"[{style}]{message}[/{style}]")

    def show_main_menu(self):
        console.clear()
        main_menu_text = Text("""
        1. Monitoreo de Precios en Tiempo Real
        2. Búsqueda de Oportunidades de Arbitraje (Combinado)
        3. Historial de Transacciones
        4. Configuración
        5. Salir
        """, justify="left")
        self.layout["header"].update(Panel("[bold]ARBITRIUM BOT - MENÚ PRINCIPAL[/]", subtitle="v2.2 Unified Arbitrage"))
        self.layout["main"].update(Panel(main_menu_text, title="Opciones Disponibles"))
        self.layout["footer"].update(self._make_footer_panel("Esperando selección..."))
        console.print(self.layout)

    def show_submenu(self, title: str):
        self.layout["header"].update(Panel(f"[bold]{title}[/]", style="green", subtitle="v2.2 Unified Arbitrage"))

    def update_opportunities_panel(self, opportunities: Sequence[Mapping[str, Any]]):
        table = Table(title="🔍 Oportunidades Detectadas", expand=True)

        if not opportunities:
            table.add_column("Estado", justify="center")
            table.add_row("\n[yellow]No se encontraron oportunidades en este ciclo.[/yellow]\n")
        else:
            table.add_column("Tipo", style="cyan", width=12)
            table.add_column("Ruta", style="magenta")
            table.add_column("DEX / Plataformas", style="yellow")
            table.add_column("Ganancia Neta", style="green", justify="right")

            for opp in opportunities[:20]:
                opp_type_str, route_str, dex_info, profit_str = "Desconocido", "", "", ""

                if 'buy_at' in opp:
                    opp_type_str = "Cross-DEX"
                    pair_tuple = opp.get('pair', ('?','?'))
                    route_str = f"{pair_tuple[0]} ↔ {pair_tuple[1]}"
                    dex_info = f"Compra: {opp['buy_at']}\nVenta: {opp['sell_at']}"
                    profit_str = f"{opp.get('profit', 0.0):.4f}%"

                elif 'profit_usdt' in opp:
                    opp_type_str = opp.get('opportunity_type', 'Triangular')
                    route_str = " → ".join(opp.get('route', []))
                    dex_info = opp.get('dex', 'N/A')
                    profit_usdt = opp.get('profit_usdt', 0.0)
                    profit_percent = opp.get('profit_percent', 0.0)
                    profit_str = f"[bold green]+${profit_usdt:.2f}[/bold green] ({profit_percent:.4f}%)"

                table.add_row(opp_type_str, route_str, dex_info, profit_str)

        self.layout["main"].update(Panel(table, border_style="magenta"))
        console.print(self.layout)

    def update_price_data(self, prices: Dict[str, float]):
        table = Table(title="📈 Precios Actualizados")
        table.add_column("Token", style="cyan")
        table.add_column("Precio (USDT)", style="green", justify="right")
        for token, price in prices.items():
            table.add_row(token, f"{price:,.8f}")
        self.layout["main"].update(Panel(table, border_style="green"))
        console.print(self.layout)

    def show_transaction_history(self):
        if not self.db_manager:
            self.display_message("[red]Error: DatabaseManager no está disponible para mostrar el historial.[/red]")
            return

        self.show_submenu("📊 Historial de Transacciones y Oportunidades")
        self.update_footer_status("Cargando historial...")

        historic_opportunities = self.db_manager.get_all_historic_opportunities()
        executed_trades = self.db_manager.get_all_executed_trades()

        historic_table = Table(title="Oportunidades Rentables Detectadas (Sin Ejecutar)", expand=True)
        historic_table.add_column("Timestamp", style="cyan")
        historic_table.add_column("Ruta", style="magenta")
        historic_table.add_column("DEX / Tipo", style="yellow")
        historic_table.add_column("Ganancia USDT", style="green", justify="right")
        historic_table.add_column("Ganancia %", style="bold green", justify="right")

        if not historic_opportunities:
            historic_table.add_row("[dim]No hay oportunidades históricas registradas.[/dim]", "", "", "", "")
        else:
            for opp in historic_opportunities:
                timestamp_str = datetime.fromtimestamp(opp['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
                route_str = " → ".join(opp['route'])
                historic_table.add_row(
                    timestamp_str,
                    route_str,
                    opp['dex'],
                    f"${opp['profit_usdt']:.4f}",
                    f"{opp['profit_percent']:.4f}%"
                )

        executed_table = Table(title="Transacciones Ejecutadas", expand=True)
        executed_table.add_column("Timestamp", style="cyan")
        executed_table.add_column("Estado", style="yellow")
        executed_table.add_column("Ruta", style="magenta")
        executed_table.add_column("Ganancia Neta USDT", style="green", justify="right")
        executed_table.add_column("Costo Gas USDT", style="red", justify="right")
        executed_table.add_column("Hash Tx", style="blue")

        if not executed_trades:
            executed_table.add_row("[dim]No hay transacciones ejecutadas registradas.[/dim]", "", "", "", "", "")
        else:
            for trade in executed_trades:
                timestamp_str = datetime.fromtimestamp(trade['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
                route_str = " → ".join(trade['route'])
                status_color = "green" if trade['status'] == 'success' else "red" if 'failed' in trade['status'] else "yellow"

                net_profit_str = f"${trade['net_profit_usdt']:.4f}" if trade['net_profit_usdt'] is not None else "N/A"
                gas_cost_str = f"${trade['gas_cost_usdt']:.6f}" if trade['gas_cost_usdt'] is not None else "N/A"
                tx_hash_str = trade['tx_hash'][:10] + "..." + trade['tx_hash'][-8:] if trade['tx_hash'] else "N/A"

                executed_table.add_row(
                    timestamp_str,
                    f"[{status_color}]{trade['status']}[/{status_color}]",
                    route_str,
                    net_profit_str,
                    gas_cost_str,
                    tx_hash_str
                )

        history_layout = Layout()
        history_layout.split_row(
            Layout(historic_table, name="historic_panel", ratio=1),
            Layout(executed_table, name="executed_panel", ratio=1)
        )

        self.layout["main"].update(Panel(history_layout, border_style="blue", title="Detalle del Historial"))
        self.update_footer_status("Historial cargado. Presiona Ctrl+C para volver.")
        console.print(self.layout)

    # --- MÉTODO FINAL CON SELECCIÓN NUMÉRICA Y LAYOUT DINÁMICO ---
    def show_configuration_menu(self):
        if not self.settings_manager:
            self.display_message("[red]Error: SettingsManager no está disponible.[/red]")
            return

        HORIZONTAL_LAYOUT_MIN_WIDTH = 120

        while True:
            console.clear()
            header_panel = Panel("⚙️ [bold]Configuración del Bot[/bold]", style="green", subtitle="v2.2 Unified Arbitrage")
            console.print(header_panel)

            current_settings = self.settings_manager.active_settings
            # Usamos una lista de claves para mantener un orden consistente y poder seleccionar por índice
            settings_keys = list(current_settings.keys())
            
            terminal_width = os.get_terminal_size().columns
            
            # --- LÓGICA DINÁMICA: ELEGIR LAYOUT SEGÚN EL ANCHO ---
            if terminal_width < HORIZONTAL_LAYOUT_MIN_WIDTH:
                # VISTA VERTICAL para terminales estrechas
                config_content = Text()
                for i, key in enumerate(settings_keys, 1):
                    value = current_settings[key]
                    config_content.append(f"{i}. ", style="yellow")
                    config_content.append(f"{key}\n", style="bold cyan")
                    config_content.append(f"   └ Valor: {value}\n\n", style="magenta")
            else:
                # VISTA HORIZONTAL (TABLA) para terminales anchas
                config_content = Table(expand=True, show_header=True, header_style="bold magenta")
                config_content.add_column("#", style="yellow", justify="right")
                config_content.add_column("Clave", style="cyan", no_wrap=True)
                config_content.add_column("Valor Actual", style="magenta")
                config_content.add_column("Tipo", style="yellow")
                
                for i, key in enumerate(settings_keys, 1):
                    value = current_settings[key]
                    config_content.add_row(str(i), key, str(value), type(value).__name__)

            main_panel = Panel(config_content, border_style="green", title="Ajustes Actuales")
            console.print(main_panel)

            footer_text = Text("Introduce un número para editar, o escribe 'salir' para volver.", justify="center")
            console.print(Panel(footer_text, border_style="blue"))

            choice = console.input("[bold green]> [/bold green]").strip()

            if choice.lower() == 'salir':
                break

            try:
                # --- PROCESAR LA SELECCIÓN NUMÉRICA ---
                choice_index = int(choice) - 1
                if not (0 <= choice_index < len(settings_keys)):
                    raise IndexError("Número fuera de rango.")
                
                real_key = settings_keys[choice_index]

            except (ValueError, IndexError):
                self.display_message(f"\n[red]Selección inválida. Por favor, introduce un número entre 1 y {len(settings_keys)}.[/red]")
                time.sleep(2)
                continue

            current_value_type = type(current_settings[real_key])
            new_value_input = console.input(f"[bold yellow]Nuevo valor para '{real_key}' > [/bold yellow]").strip()

            try:
                if current_value_type is int:
                    new_value = int(new_value_input)
                elif current_value_type is float:
                    new_value = float(new_value_input)
                elif current_value_type is bool:
                    new_value = new_value_input.lower() in ('true', '1', 'yes', 'si', 's')
                else:
                    new_value = new_value_input

                current_settings[real_key] = new_value
                self.settings_manager.save_settings(current_settings)
                self.display_message(f"\n[green]'{real_key}' actualizado a: {new_value}[/green]")
            except (ValueError, TypeError):
                self.display_message(f"\n[red]Error: '{new_value_input}' no es un valor válido.[/red]")
            
            time.sleep(2)