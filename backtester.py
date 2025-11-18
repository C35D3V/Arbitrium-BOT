import sqlite3
import json
from pathlib import Path
from rich.console import Console
from rich.table import Table

# --- PARÁMETROS DE LA SIMULACIÓN ---
# Modifica estos valores para probar diferentes estrategias
TEST_CONFIG = {
    "MIN_PROFIT_THRESHOLD_USDT": 1.00,  # Prueba con un umbral de ganancia de $1.00
    "INCLUDE_FAILED_TRADES": False      # ¿Consideramos los costes de trades fallidos en el cálculo?
}
# -----------------------------------

console = Console()
DB_PATH = Path(__file__).parent / "data" / "arbitrium.db"

def run_backtest():
    """
    Ejecuta una simulación de backtesting usando los datos históricos
    y la configuración de prueba definida arriba.
    """
    console.print("[bold cyan]🚀 Iniciando Backtester de Arbitrium BOT 🚀[/bold cyan]")

    if not DB_PATH.exists():
        console.print(f"[bold red]Error: No se encontró la base de datos en '{DB_PATH}'[/bold red]")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row # Permite acceder a las columnas por nombre
        cursor = conn.cursor()

        # 1. Obtener todas las oportunidades y ejecuciones
        cursor.execute("SELECT * FROM historic_opportunities")
        opportunities = cursor.fetchall()

        cursor.execute("SELECT * FROM executed_trades")
        # Creamos un diccionario para buscar ejecuciones fácilmente por su ID de correlación
        executed_trades = {trade['correlation_id']: trade for trade in cursor.fetchall()}

        conn.close()

    except Exception as e:
        console.print(f"[bold red]Error al leer la base de datos: {e}[/bold red]")
        return

    if not opportunities or not executed_trades:
        console.print("[yellow]No hay suficientes datos en la base de datos para realizar el backtest.[/yellow]")
        return

    # 2. Simular la estrategia con la nueva configuración
    hypothetical_trades_count = 0
    hypothetical_net_profit = 0.0
    
    actual_trades_count = 0
    actual_net_profit = 0.0

    table = Table(title=f"Resultados del Backtest (Umbral de Ganancia: ${TEST_CONFIG['MIN_PROFIT_THRESHOLD_USDT']:.2f})")
    table.add_column("Ruta", style="cyan")
    table.add_column("Ganancia Detectada", style="magenta")
    table.add_column("Estado Real", style="yellow")
    table.add_column("Ganancia Neta Real", style="green")
    table.add_column("¿Ejecutado en Simulación?", style="blue")

    for opp in opportunities:
        trade_result = executed_trades.get(opp['correlation_id'])
        
        # Lógica de la simulación
        would_have_executed = opp['profit_usdt'] >= TEST_CONFIG['MIN_PROFIT_THRESHOLD_USDT']
        
        if would_have_executed:
            hypothetical_trades_count += 1
            if trade_result and trade_result['net_profit_usdt'] is not None:
                # Solo sumar si el trade fue exitoso
                if trade_result['status'] == 'success':
                    hypothetical_net_profit += trade_result['net_profit_usdt']
                # Si queremos ser más estrictos, también restamos el coste de los fallidos
                elif TEST_CONFIG['INCLUDE_FAILED_TRADES'] and trade_result['gas_cost_usdt'] is not None:
                    hypothetical_net_profit -= trade_result['gas_cost_usdt']

        # Lógica de los resultados reales
        if trade_result:
            actual_trades_count += 1
            if trade_result['net_profit_usdt'] is not None and trade_result['status'] == 'success':
                actual_net_profit += trade_result['net_profit_usdt']
            elif trade_result['gas_cost_usdt'] is not None: # Sumar coste de transacciones fallidas reales
                 actual_net_profit -= trade_result['gas_cost_usdt']


        table.add_row(
            opp['route'],
            f"${opp['profit_usdt']:.4f}",
            trade_result['status'] if trade_result else "N/A",
            f"${trade_result['net_profit_usdt']:.4f}" if trade_result and trade_result['net_profit_usdt'] is not None else "N/A",
            "[bold green]Sí[/bold green]" if would_have_executed else "[dim]No[/dim]"
        )

    console.print(table)

    # 3. Mostrar el reporte final
    summary_table = Table(title="Resumen Comparativo del Backtest")
    summary_table.add_column("Métrica", style="bold")
    summary_table.add_column("Rendimiento Real (Histórico)", style="yellow")
    summary_table.add_column("Rendimiento Simulado (Backtest)", style="cyan")

    summary_table.add_row("Trades Ejecutados", str(actual_trades_count), str(hypothetical_trades_count))
    summary_table.add_row("Ganancia Neta Total (USDT)", f"[bold green]${actual_net_profit:,.2f}[/bold green]", f"[bold green]${hypothetical_net_profit:,.2f}[/bold green]")
    
    console.print(summary_table)


if __name__ == "__main__":
    run_backtest()