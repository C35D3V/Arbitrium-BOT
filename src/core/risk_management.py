import asyncio
from typing import Dict, Any, TYPE_CHECKING, Optional, List, Tuple
from core.logger import ArbitriumLogger
import math
from web3 import Web3
import json

if TYPE_CHECKING:
    from bsc.data_fetcher import BSCDataFetcher
    from bsc.execution import BSCTransactionExecutor
    from core.settings_manager import SettingsManager

class RiskManager:
    """
    Centraliza todas las comprobaciones de riesgo antes de ejecutar una transacción.
    """
    def __init__(self, fetcher: "BSCDataFetcher", executor: "BSCTransactionExecutor", settings_manager: "SettingsManager"):
        self.fetcher = fetcher
        self.executor = executor
        self.settings_manager = settings_manager
        self.logger = ArbitriumLogger('risk_manager', 'arbitrium.log')
        self.logger.info("RiskManager inicializado.")

    async def calculate_dynamic_slippage(self, token_in_sym: str, investment_usdt: float, pools_data: Dict[tuple, Dict[str, Any]]) -> float:
        """
        Calcula un slippage dinámico basado en el impacto del trade en la liquidez del pool.
        Retorna el slippage como un porcentaje (ej: 1.5 para 1.5%).
        """
        try:
            # Obtener el precio del token para estimar la liquidez del pool en USDT
            prices = await self.fetcher.get_current_prices()
            token_in_price_usdt = prices.get(token_in_sym)

            if not token_in_price_usdt or token_in_price_usdt == 0:
                return self.settings_manager.get_setting("DEFAULT_SLIPPAGE_TOLERANCE") * 100

            # Encontrar el pool correspondiente (simplificado, asumiendo que pools_data tiene la info)
            # Esta parte dependerá de cómo se estructure 'pools_data', pero la idea es buscar el pool
            # que contiene token_in_sym
            # ... Lógica para encontrar las reservas del pool ...
            # reserve_in_raw, reserve_out_raw = ...
            
            # Para este ejemplo, asumiremos que encontramos las reservas del token de entrada
            # y que podemos estimar la liquidez total del pool en USDT.
            # Esta es una simplificación. Una implementación real necesitaría buscar el pool exacto.
            
            # Simulación simple de encontrar liquidez (esto necesitaría una implementación más robusta)
            # Supongamos que encontramos un pool con 1,000,000 USDT de liquidez
            pool_liquidity_usdt = 1000000.0 

            if pool_liquidity_usdt == 0:
                return self.settings_manager.get_setting("MAX_SLIPPAGE_PERCENT")

            # --- Fórmula de Slippage Dinámico ---
            base_slippage = self.settings_manager.get_setting("BASE_SLIPPAGE_PERCENT")
            impact_factor = self.settings_manager.get_setting("LIQUIDITY_IMPACT_FACTOR")
            max_slippage = self.settings_manager.get_setting("MAX_SLIPPAGE_PERCENT")

            # El impacto es la proporción del tamaño de nuestra inversión sobre la liquidez total del pool
            trade_impact_percentage = (investment_usdt / pool_liquidity_usdt) * 100
            
            # El slippage dinámico es el base + el impacto multiplicado por nuestro factor de ajuste
            dynamic_slippage = base_slippage + (trade_impact_percentage * impact_factor)
            
            # Nos aseguramos de no superar el tope de seguridad
            final_slippage = min(dynamic_slippage, max_slippage)

            self.logger.debug(f"Slippage dinámico calculado para {token_in_sym}: {final_slippage:.2f}% (Base: {base_slippage}%, Impacto del Trade: {trade_impact_percentage:.4f}%)")
            
            return final_slippage

        except Exception as e:
            self.logger.warning(f"No se pudo calcular el slippage dinámico: {e}. Usando slippage por defecto.")
            return self.settings_manager.get_setting("DEFAULT_SLIPPAGE_TOLERANCE") * 100

    def validate_opportunity(self, opportunity: Dict[str, Any]) -> bool:
        """
        Punto de entrada principal para validar una oportunidad de arbitraje.
        """
        self.logger.debug(f"Validando oportunidad: {opportunity.get('route')}")
        return True

    #La función ahora es asíncrona
    async def get_optimal_investment_size(self, opportunity: Dict[str, Any], pools_data: Dict[tuple, Dict[str, Any]], bnb_price_usdt: float) -> float:
        """
        Calcula la cantidad óptima a invertir para maximizar la ganancia neta.
        """
        # ---Usar la ruta detallada para la simulación ---
        route_detailed = opportunity.get('route_detailed')
        if not route_detailed:
            self.logger.warning("La oportunidad no contiene una 'route_detailed' para calcular la inversión óptima.")
            return 0.0
        
        route_simple = [step['token'] for step in route_detailed]
        if len(route_simple) < 2:
            self.logger.warning("Ruta de oportunidad inválida para calcular inversión óptima.")
            return 0.0

        start_token_sym = route_simple[0]
        # Se añade 'await' y se obtiene el diccionario de precios primero
        prices = await self.fetcher.get_current_prices()
        start_token_usdt_price = prices.get(start_token_sym)

        if not start_token_usdt_price or start_token_usdt_price == 0:
            self.logger.warning(f"No se pudo obtener precio de {start_token_sym} para optimización. Saltando.")
            return 0.0

        # La función interna ahora debe ser 'async' para usar 'await'
        async def _calculate_net_profit(amount_in_native: float) -> float:
            if amount_in_native <= 0:
                return 0.0
            
            # Se debe esperar el resultado de la función asíncrona
            simulated_final_amount_native = await self._simulate_path_amount_out(amount_in_native, route_detailed, pools_data)

            if simulated_final_amount_native is None or simulated_final_amount_native <= amount_in_native:
                return 0.0

            gross_profit_native = simulated_final_amount_native - amount_in_native
            gross_profit_usdt = gross_profit_native * start_token_usdt_price

            average_gas_per_swap = self.settings_manager.get_setting("AVERAGE_GAS_PER_SWAP", 150000)
            estimated_gas_price_wei = self.fetcher.get_gas_price() or self.fetcher.w3.to_wei(5, 'gwei')
            number_of_swaps = len(route_simple) - 1
            estimated_gas_cost_bnb = self.fetcher.w3.from_wei((average_gas_per_swap * number_of_swaps) * estimated_gas_price_wei, 'ether')
            estimated_gas_cost_usdt = float(estimated_gas_cost_bnb) * bnb_price_usdt
            
            return gross_profit_usdt - estimated_gas_cost_usdt

        # Búsqueda Ternaria
        low_native = (0.01 / start_token_usdt_price) if start_token_usdt_price > 0 else 0.001
        max_investment_usdt = self.settings_manager.get_setting("MAX_INVESTMENT_OPTIMIZATION_USDT", 5000.0)
        high_native = (max_investment_usdt / start_token_usdt_price) if start_token_usdt_price > 0 else 5000.0
        
        if low_native >= high_native:
            self.logger.warning("Rango de búsqueda de inversión inválido (low >= high). Ajustando high.")
            high_native = low_native * 100 if low_native > 0 else 100.0

        iterations = self.settings_manager.get_setting("OPTIMIZATION_ITERATIONS", 50)
        
        self.logger.debug(f"Iniciando búsqueda ternaria para ruta: {' -> '.join(route_simple)} en rango [{low_native:.4f}, {high_native:.4f}] {start_token_sym}")

        if iterations < 2:
            iterations = 2

        for i in range(iterations):
            if high_native - low_native < 1e-12 * low_native:
                self.logger.debug(f"Búsqueda ternaria terminada temprano: rango muy pequeño en iteración {i+1}.")
                break
            
            m1_native = low_native + (high_native - low_native) / 3
            m2_native = high_native - (high_native - low_native) / 3
            
            # Las llamadas a la función interna ahora deben ser esperadas
            profit1 = await _calculate_net_profit(m1_native)
            profit2 = await _calculate_net_profit(m2_native)

            if profit1 < profit2:
                low_native = m1_native
            else:
                high_native = m2_native
                
            self.logger.debug(f"Iter {i+1}/{iterations}: Rango [{low_native:.6f}, {high_native:.6f}], M1_Profit=${profit1:.4f}, M2_Profit=${profit2:.4f}")

        optimal_amount_in_native = (low_native + high_native) / 2
        # La llamada final también debe ser esperada
        max_net_profit = await _calculate_net_profit(optimal_amount_in_native)

        min_profit_threshold = self.settings_manager.get_setting("MIN_PROFIT_THRESHOLD_USDT", 0.50)

        if max_net_profit > min_profit_threshold:
            self.logger.info(
                f"Inversión óptima encontrada para ruta {' -> '.join(route_simple)}: "
                f"{optimal_amount_in_native:.6f} {start_token_sym} "
                f"(Ganancia Neta Estimada: ${max_net_profit:.4f} USDT)"
            )
            return optimal_amount_in_native
        else:
            self.logger.debug(f"No se encontró inversión rentable (> ${min_profit_threshold}) para la ruta. Máx ganancia encontrada: ${max_net_profit:.4f}")
            return 0.0

    async def _simulate_path_amount_out(self, amount_in_native: float, route_detailed: List[Dict[str, Any]], pools_data: Dict[tuple, Dict[str, Any]]) -> Optional[float]:
        current_amount_native = amount_in_native

        for i in range(len(route_detailed) - 1):
            token_in_sym = route_detailed[i]['token']
            token_out_sym = route_detailed[i+1]['token']
            dex_for_swap = route_detailed[i+1].get('dex')

            token_in_addr = self.fetcher.tokens_by_symbol.get(token_in_sym)
            token_out_addr = self.fetcher.tokens_by_symbol.get(token_out_sym)

            if not (token_in_addr and token_out_addr):
                self.logger.warning(f"Tokens {token_in_sym} o {token_out_sym} no encontrados para simulación de ruta.")
                return None

            pool_key = tuple(sorted((token_in_addr, token_out_addr)))
            pool_data = None
            if dex_for_swap:
                for key, data in pools_data.items():
                    if key == pool_key and data.get('dex_name') == dex_for_swap:
                        pool_data = data
                        break
            
            if not pool_data:
                pool_data = pools_data.get(pool_key)
            
            if not pool_data:
                dex_msg = f" en {dex_for_swap}" if dex_for_swap else ""
                self.logger.debug(f"No hay datos de pool para {token_in_sym}-{token_out_sym}{dex_msg} en la simulación.")
                return None

            reserves = pool_data.get('reserves')
            dex_fee = pool_data.get('fee', 0.0025) 
            pool_token0_addr = pool_data.get('token0')

            if reserves is None or dex_fee is None or pool_token0_addr is None:
                self.logger.warning(f"Datos de reservas, fee o token0 incompletos para pool {pool_key} en simulación.")
                return None

            if pool_token0_addr == token_in_addr:
                reserve_in_raw, reserve_out_raw = reserves[0], reserves[1]
            else:
                reserve_in_raw, reserve_out_raw = reserves[1], reserves[0]

            # ---Añadido 'await' para obtener el valor entero ---
            decimals_in = await self.fetcher._get_token_decimals(token_in_addr)
            decimals_out = await self.fetcher._get_token_decimals(token_out_addr)

            amount_in_wei = int(current_amount_native * (10**decimals_in))

            if reserve_in_raw == 0:
                self.logger.debug(f"Reserva de entrada es cero para {token_in_sym}-{token_out_sym}. No hay liquidez.")
                return 0.0

            amount_in_with_fee = amount_in_wei * int((1 - dex_fee) * 10000) // 10000
            numerator = amount_in_with_fee * reserve_out_raw
            denominator = reserve_in_raw + amount_in_with_fee

            if denominator == 0:
                return 0.0

            amount_out_wei = numerator // denominator
            current_amount_native = float(amount_out_wei) / (10**decimals_out)

            if current_amount_native == 0:
                self.logger.debug(f"Simulación de swap resultó en 0. La ruta está rota en {token_in_sym}->{token_out_sym}.")
                return None

        return current_amount_native