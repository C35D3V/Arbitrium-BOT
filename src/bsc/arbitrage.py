import math
import time
import uuid
from collections import defaultdict, deque
from typing import List, Dict, Optional, Tuple, Any, TypedDict, TYPE_CHECKING, cast, Union, Sequence
from itertools import combinations

from core.database import DatabaseManager
from core.logger import ArbitriumLogger
from core.route_generator import RouteGenerator

if TYPE_CHECKING:
    from bsc.data_fetcher import BSCDataFetcher
    from bsc.pool_monitor import PoolMonitor
    from core.interface import BotInterface
    from bsc.execution import BSCTransactionExecutor
    from core.risk_management import RiskManager
    from core.settings_manager import SettingsManager

#La oportunidad puede contener una ruta detallada y un ID de correlación
class Opportunity(TypedDict):
    route: List[str]
    route_detailed: List[Dict[str, Any]]
    dex: str
    profit_usdt: float
    profit_percent: float
    timestamp: int
    opportunity_type: str
    correlation_id: str 

class CrossDEXOpportunity(TypedDict):
    pair: Tuple[str, str]
    route: List[str]
    buy_at: str
    sell_at: str
    profit: float
    buy_price: float
    sell_price: float
    timestamp: int

class StablecoinOpportunity(TypedDict):
    pair: Tuple[str, str]
    route: List[str]
    buy_at: str
    sell_at: str
    profit_percent: float
    buy_price: float
    sell_price: float
    timestamp: int
    opportunity_type: str

class CombinedArbitrage:
    def __init__(self, fetcher: "BSCDataFetcher", interface: "BotInterface", db_manager: "DatabaseManager",
                 executor: "BSCTransactionExecutor", risk_manager: "RiskManager", settings_manager: "SettingsManager"):
        self.fetcher = fetcher
        self.interface = interface
        self.db_manager = db_manager
        self.executor = executor
        self.risk_manager = risk_manager
        self.settings_manager = settings_manager
        self.logger = ArbitriumLogger('combined_arb', 'arbitrium.log')

        self.multi_step_arbitrage = MultiStepArbitrage(fetcher, interface, db_manager, self.executor, self.risk_manager, self.settings_manager)
        self.cross_dex_arbitrage = CrossDEXArbitrage(fetcher, self.settings_manager)
        self.stablecoin_arbitrage = StablecoinArbitrage(fetcher, self.settings_manager)

    async def initialize(self, pool_monitor: "PoolMonitor"):
        await self.multi_step_arbitrage.initialize(pool_monitor)
        token_pairs = await self._get_existing_token_pairs(pool_monitor)
        self.cross_dex_arbitrage.set_default_token_pairs(token_pairs)
        self.logger.info(f"Arbitraje Cross-DEX inicializado con {len(token_pairs)} pares de liquidez existentes y verificados.")

    async def find_opportunities(self) -> Sequence[Union[Opportunity, CrossDEXOpportunity, StablecoinOpportunity]]:
        all_opportunities: List[Union[Opportunity, CrossDEXOpportunity, StablecoinOpportunity]] = []
        
        #Se añade 'await' para esperar las funciones asíncronas
        cross_dex_opps = await self.cross_dex_arbitrage.find_opportunities()
        if cross_dex_opps:
            all_opportunities.extend(cross_dex_opps)

        #Se añade 'await' para esperar las funciones asíncronas
        stablecoin_opps = await self.stablecoin_arbitrage.find_opportunities()
        if stablecoin_opps:
            all_opportunities.extend(stablecoin_opps)

        all_opportunities.sort(
            key=lambda opp: opp.get('profit', opp.get('profit_percent', 0)),
            reverse=True
        )

        return all_opportunities

    async def on_pool_update(self, pair_address: str):
        await self.multi_step_arbitrage.on_pool_update(pair_address)

    async def _get_existing_token_pairs(self, pool_monitor: "PoolMonitor") -> List[Tuple[str, str]]:
        if not pool_monitor:
            return []

        live_pools = await pool_monitor.get_all_live_pools()
        existing_pairs = set()

        for pool_key in live_pools.keys():
            token0_addr, token1_addr = pool_key
            token0_sym = self.fetcher.symbols_by_address.get(token0_addr)
            token1_sym = self.fetcher.symbols_by_address.get(token1_addr)

            if token0_sym and token1_sym:
                existing_pairs.add(tuple(sorted((token0_sym, token1_sym))))

        return [tuple(p) for p in existing_pairs]


class CrossDEXArbitrage:
    def __init__(self, fetcher: "BSCDataFetcher", settings_manager: "SettingsManager"):
        self.fetcher = fetcher
        self.settings_manager = settings_manager
        self.logger = ArbitriumLogger('cross_dex_arb', 'arbitrium.log')
        self.token_pairs: List[Tuple[str, str]] = []
        self.min_profit_percentage = self.settings_manager.get_setting("MIN_CROSS_DEX_PROFIT_PERCENTAGE", 0.2)

    def set_default_token_pairs(self, pairs: List[Tuple[str, str]]):
        self.token_pairs = pairs

    #La función ahora es asíncrona para permitir 'await'
    async def find_opportunities(self) -> Sequence[CrossDEXOpportunity]:
        opportunities: List[CrossDEXOpportunity] = []
        self.logger.debug(f"Buscando oportunidades Cross-DEX en {len(self.token_pairs)} pares de tokens.")

        for token_a_sym, token_b_sym in self.token_pairs:
            #Se añade 'await' para esperar la obtención de precios
            prices_a_to_b = await self.fetcher.get_prices_for_pair_across_dexs(token_a_sym, token_b_sym, amount_in=1.0)
            prices_b_to_a = await self.fetcher.get_prices_for_pair_across_dexs(token_b_sym, token_a_sym, amount_in=1.0)

            if not prices_a_to_b or not prices_b_to_a:
                continue

            best_buy_b_with_a_dex = max(prices_a_to_b, key=lambda d: prices_a_to_b.get(d) or -1.0)
            max_b_for_a_price = prices_a_to_b.get(best_buy_b_with_a_dex)

            best_sell_b_for_a_dex = max(prices_b_to_a, key=lambda d: prices_b_to_a.get(d) or -1.0)
            max_a_for_b_price = prices_b_to_a.get(best_sell_b_for_a_dex)

            if (max_b_for_a_price is not None and max_a_for_b_price is not None and
                best_buy_b_with_a_dex != best_sell_b_for_a_dex):

                return_for_1_a = max_b_for_a_price * max_a_for_b_price
                profit_percentage = (return_for_1_a - 1.0) * 100

                if profit_percentage > self.min_profit_percentage:
                    self.logger.info(
                        f"¡Oportunidad Cross-DEX detectada! Par: {token_a_sym}/{token_b_sym} "
                        f"Ganancia: {profit_percentage:.4f}%"
                    )
                    opportunities.append(CrossDEXOpportunity(
                        pair=(token_a_sym, token_b_sym),
                        route=[token_a_sym, token_b_sym, token_a_sym],
                        buy_at=best_buy_b_with_a_dex,
                        sell_at=best_sell_b_for_a_dex,
                        buy_price=max_b_for_a_price,
                        sell_price=max_a_for_b_price,
                        profit=round(profit_percentage, 4),
                        timestamp=int(time.time())
                    ))
        return opportunities


class MultiStepArbitrage:
    def __init__(self, fetcher: "BSCDataFetcher", interface: "BotInterface", db_manager: "DatabaseManager",
                 executor: "BSCTransactionExecutor", risk_manager: "RiskManager", settings_manager: "SettingsManager"):
        self.fetcher = fetcher
        self.interface = interface
        self.db_manager = db_manager
        self.executor = executor
        self.risk_manager = risk_manager
        self.settings_manager = settings_manager
        self.logger = ArbitriumLogger('multistep_arb', 'arbitrium.log')
        
        self.all_tokens_by_addr = {v: k for k, v in self.fetcher.tokens_by_symbol.items()}
        self.pool_monitor: Optional["PoolMonitor"] = None
        
        self.nodes = set()
        self.edges = []
        self.graph = defaultdict(list)
        
        self._analyzed_cycles = set()
        self._is_initialized = False

    async def initialize(self, pool_monitor: "PoolMonitor"):
        self.pool_monitor = pool_monitor
        self.logger.info("Inicializando MultiStepArbitrage y construyendo grafo de rutas inicial...")
        await self._build_initial_graph()
        self._is_initialized = True
        self.logger.info(f"Grafo inicial construido con {len(self.nodes)} nodos y {len(self.edges)} aristas.")

    async def _build_initial_graph(self):
        if not self.pool_monitor: return
        
        all_pools = await self.pool_monitor.get_all_live_pools()
        if not all_pools:
            self.logger.warning("No se encontraron pools para construir el grafo inicial.")
            return

        for pool_key, pool_data in all_pools.items():
            token0_addr, token1_addr = pool_key
            token0_sym = self.all_tokens_by_addr.get(token0_addr)
            token1_sym = self.all_tokens_by_addr.get(token1_addr)

            if not (token0_sym and token1_sym): continue

            self.nodes.update([token0_sym, token1_sym])
            reserves, fee = pool_data.get('reserves'), pool_data.get('fee', 0.0025)

            if not reserves or reserves[0] == 0 or reserves[1] == 0: continue
            
            dex_name = pool_data.get('dex_name', 'Unknown')

            rate0_to_1 = (reserves[1] / reserves[0]) * (1 - fee)
            if rate0_to_1 > 0:
                edge1 = {'from': token0_sym, 'to': token1_sym, 'weight': -math.log(rate0_to_1), 'dex': dex_name}
                self.edges.append(edge1)
                self.graph[token0_sym].append(edge1)

            rate1_to_0 = (reserves[0] / reserves[1]) * (1 - fee)
            if rate1_to_0 > 0:
                edge2 = {'from': token1_sym, 'to': token0_sym, 'weight': -math.log(rate1_to_0), 'dex': dex_name}
                self.edges.append(edge2)
                self.graph[token1_sym].append(edge2)

    async def on_pool_update(self, pair_address: str):
        if not self.pool_monitor or not self._is_initialized: return

        pool_info = self.pool_monitor.pools_info.get(pair_address)
        live_reserves = self.pool_monitor.live_reserves.get(pair_address)
        
        if not pool_info or not live_reserves: return

        token0_addr, token1_addr = pool_info['token0'], pool_info['token1']
        token0_sym, token1_sym = self.all_tokens_by_addr.get(token0_addr), self.all_tokens_by_addr.get(token1_addr)
        
        if not token0_sym or not token1_sym: return
        
        self.logger.debug(f"Actualización incremental del grafo para el par: {token0_sym}/{token1_sym}")

        self.edges = [e for e in self.edges if not (e['from'] in {token0_sym, token1_sym} and e['to'] in {token0_sym, token1_sym})]
        
        if live_reserves[0] == 0 or live_reserves[1] == 0:
            self.logger.warning(f"Reservas para {token0_sym}/{token1_sym} son cero. Aristas eliminadas.")
        else:
            fee, dex_name = pool_info.get('fee', 0.0025), pool_info.get('dex_name', 'Unknown')
            
            rate0_to_1 = (live_reserves[1] / live_reserves[0]) * (1 - fee)
            if rate0_to_1 > 0:
                self.edges.append({'from': token0_sym, 'to': token1_sym, 'weight': -math.log(rate0_to_1), 'dex': dex_name})
            
            rate1_to_0 = (live_reserves[0] / live_reserves[1]) * (1 - fee)
            if rate1_to_0 > 0:
                self.edges.append({'from': token1_sym, 'to': token0_sym, 'weight': -math.log(rate1_to_0), 'dex': dex_name})

        await self.find_negative_cycles()

    async def find_negative_cycles(self):
        if not self.pool_monitor: return
        start_token = "USDT"
        if start_token not in self.nodes: return
        
        distances = {node: float('inf') for node in self.nodes}
        predecessors: Dict[str, Optional[Dict[str, Any]]] = {node: None for node in self.nodes}
        distances[start_token] = 0

        for i in range(len(self.nodes)):
            for edge in self.edges:
                u, v, w = edge['from'], edge['to'], edge['weight']
                
                if distances.get(u, float('inf')) != float('inf') and distances[u] + w < distances.get(v, float('inf')):
                    if i == len(self.nodes) - 1: # Ciclo negativo encontrado
                        for cycle_node in self.nodes:
                            temp_distances = distances.copy()
                            for _ in range(len(self.nodes)):
                                if temp_distances[u] + w < temp_distances[v]:
                                    temp_distances[v] = temp_distances[u] + w
                            if temp_distances[cycle_node] < distances[cycle_node]:
                                predecessor_edge = predecessors[cycle_node]
                                path, visited = [], set()

                                while predecessor_edge and predecessor_edge['from'] not in visited:
                                    path.insert(0, {'token': predecessor_edge['to'], 'dex': predecessor_edge['dex']})
                                    visited.add(predecessor_edge['from'])
                                    predecessor_edge = predecessors.get(predecessor_edge['from'])
                                
                                if predecessor_edge:
                                    path.insert(0, {'token': predecessor_edge['from']})
                                    
                                    simple_path = [step['token'] for step in path]
                                    cycle_key = tuple(sorted(set(simple_path)))
                                    if cycle_key in self._analyzed_cycles: continue
                                    self._analyzed_cycles.add(cycle_key)

                                    all_pools = await self.pool_monitor.get_all_live_pools()
                                    bnb_price = await self._get_bnb_price(all_pools)
                                    
                                    if bnb_price:
                                        opportunity = await self._analyze_route_profitability(path, "Bellman-Ford", all_pools, bnb_price)
                                        if opportunity:
                                            self.interface.update_opportunities_panel([opportunity])
                                            return
                        return
                    else:
                        distances[v] = distances[u] + w
                        predecessors[v] = edge
                        
        self._analyzed_cycles.clear()

    async def find_long_path_opportunities(self) -> List[Opportunity]:
        if not self._is_initialized or not self.pool_monitor: return []
        self.logger.debug("Iniciando búsqueda activa de rutas de 3 y 4 swaps...")
        all_opportunities: List[Opportunity] = []
        
        graph = defaultdict(set)
        for edge in self.edges:
            graph[edge['from']].add(edge['to'])
            graph[edge['to']].add(edge['from'])

        start_token = "USDT"
        if start_token not in graph: return []

        all_pools = await self.pool_monitor.get_all_live_pools()
        bnb_price = await self._get_bnb_price(all_pools)
        if not bnb_price: return []

        for path_length in [3, 4]:
            self.logger.debug(f"Buscando rutas de {path_length} swaps...")
            potential_routes = RouteGenerator.find_paths_of_length_n(graph, start_token, path_length)
            
            if not potential_routes: continue
            self.logger.info(f"Se encontraron {len(potential_routes)} rutas potenciales de {path_length} swaps.")

            for route in potential_routes:
                detailed_route = [{'token': token} for token in route]
                opportunity = await self._analyze_route_profitability(detailed_route, "Path-Finder", all_pools, bnb_price)
                if opportunity:
                    all_opportunities.append(opportunity)
        
        return all_opportunities

    async def _analyze_route_profitability(self, route_detailed: List[Dict[str, Any]], opportunity_type: str, pools: Dict, bnb_price: float) -> Optional[Opportunity]:
        route_simple = [step['token'] for step in route_detailed]
        num_swaps = len(route_simple) - 1
        if num_swaps <= 1: return None

        temp_opportunity_for_risk_calc = {'route_detailed': route_detailed}
        amount_in_native_units = await self.risk_manager.get_optimal_investment_size(
            opportunity=temp_opportunity_for_risk_calc, pools_data=pools, bnb_price_usdt=bnb_price)

        if amount_in_native_units <= 0: return None

        token_in_addr = self.fetcher.tokens_by_symbol.get(route_simple[0])
        if not token_in_addr: return None
        decimals_in = await self.fetcher._get_token_decimals(token_in_addr)
        amount_in_wei = int(amount_in_native_units * (10**decimals_in))

        # --- SIMULACIÓN DE RUTA ---
        final_amount_native = amount_in_native_units
        for i in range(num_swaps):
            token_in_sym, token_out_sym = route_simple[i], route_simple[i+1]
            dex_for_swap = route_detailed[i+1].get('dex')
            
            amount_out_native = await self._get_amount_out(final_amount_native, token_in_sym, token_out_sym, pools, dex_to_use=dex_for_swap)
            if amount_out_native is None or amount_out_native == 0: return None
            
            final_amount_native = amount_out_native

        # --- CÁLCULO DE GANANCIA ---
        prices = await self.fetcher.get_current_prices()
        start_token_usdt_price = prices.get(route_simple[0])
        if not start_token_usdt_price or start_token_usdt_price == 0: return None

        initial_investment_usdt = amount_in_native_units * start_token_usdt_price
        gross_profit_usdt = (final_amount_native * start_token_usdt_price) - initial_investment_usdt
        
        estimated_gas_price_wei = self.fetcher.get_gas_price() or self.fetcher.w3.to_wei(5, 'gwei')
        average_gas_per_swap = self.settings_manager.get_setting("AVERAGE_GAS_PER_SWAP", 150000)
        
        total_estimated_gas = average_gas_per_swap * num_swaps
        
        estimated_gas_cost_bnb = self.fetcher.w3.from_wei(total_estimated_gas * estimated_gas_price_wei, 'ether')
        estimated_gas_cost_usdt = float(estimated_gas_cost_bnb) * bnb_price
        net_profit_usdt = gross_profit_usdt - estimated_gas_cost_usdt

        min_profit_threshold = self.settings_manager.get_setting("MIN_PROFIT_THRESHOLD_USDT", 0.50)
        if net_profit_usdt > min_profit_threshold:
            correlation_id = str(uuid.uuid4())
            log_extra = {'correlation_id': correlation_id}
            profit_percent = (net_profit_usdt / initial_investment_usdt) * 100 if initial_investment_usdt > 0 else 0.0

            opportunity: Opportunity = {
                "route": route_simple,
                "route_detailed": route_detailed,
                "dex": opportunity_type,
                "profit_usdt": round(net_profit_usdt, 4),
                "profit_percent": round(profit_percent, 4),
                "timestamp": int(time.time()),
                "opportunity_type": f"{num_swaps}-Step",
                "correlation_id": correlation_id
            }
            self.logger.info(f"¡Oportunidad Encontrada! Ruta: {' → '.join(route_simple)} | Ganancia Neta: ${net_profit_usdt:.2f}", extra=log_extra)
            self.db_manager.add_historic_opportunity(cast(Dict[str, Any], opportunity))

            # --- LÓGICA DE EJECUCIÓN DIFERENCIADA ---
            dex_names_in_route = {step.get('dex') for step in route_detailed[1:] if step.get('dex')}
            is_single_dex_route = len(dex_names_in_route) == 1

            if is_single_dex_route:
                dex_name_optional = dex_names_in_route.pop()

                # --- VALIDACIÓN EXPLÍCITA PARA dex_name ---
                if not dex_name_optional:
                    self.logger.error("Nombre del DEX inválido o no encontrado en la ruta de un solo DEX. Abortando.", extra=log_extra)
                    return opportunity
                dex_name = dex_name_optional # A partir de aquí, Pylance sabe que dex_name es de tipo str

                self.logger.info(f"Ruta de un solo DEX ({dex_name}) detectada...", extra=log_extra)
                
                token_out_addr = self.fetcher.tokens_by_symbol.get(route_simple[-1])
                if not token_out_addr: return opportunity
                decimals_out = await self.fetcher._get_token_decimals(token_out_addr)

                # --- OBTENER SLIPPAGE DINÁMICO ---
                dynamic_slippage_percent = await self.risk_manager.calculate_dynamic_slippage(
                    route_simple[0],
                    initial_investment_usdt,
                    pools
                )
                dynamic_slippage_tolerance = dynamic_slippage_percent / 100.0
                
                amount_out_min_wei = int((final_amount_native * (1 - dynamic_slippage_tolerance)) * (10**decimals_out))
                
                router_address = self.fetcher.dex_routers.get(dex_name)
                router_abi = self.fetcher.get_abi("router")
                # Creamos una lista temporal que puede contener Nones
                path_addresses_optional = [self.fetcher.tokens_by_symbol.get(s) for s in route_simple]

                # --- VALIDACIONES EXPLÍCITAS ---
                # 1. Validar la dirección del router
                if not router_address:
                    self.logger.error(f"No se encontró la dirección del router para el DEX '{dex_name}'. Abortando.", extra=log_extra)
                    return opportunity
                
                # 2. Validar el ABI del router
                if not router_abi:
                    self.logger.error("No se encontró el ABI del router. Abortando.", extra=log_extra)
                    return opportunity
                
                # 3. Validar que todas las direcciones del path se encontraron
                if any(addr is None for addr in path_addresses_optional):
                    self.logger.error(f"No se pudo resolver una o más direcciones de token en la ruta: {route_simple}. Abortando.", extra=log_extra)
                    return opportunity

                # En este punto, sabemos que ninguna de las variables es None.
                # Creamos la lista final con el tipo correcto (List[str])
                path_addresses: List[str] = [addr for addr in path_addresses_optional if addr is not None]

                await self.executor.execute_swap(
                    router_address=router_address,
                    router_abi=router_abi,
                    amount_in=amount_in_wei,
                    amount_out_min=amount_out_min_wei,
                    path=path_addresses,
                    to_address=self.executor.account.address,
                    deadline=int(time.time()) + 300,
                    dex_name=dex_name,
                    gross_profit_usdt=gross_profit_usdt,
                    correlation_id=correlation_id
                )
            else:
                self.logger.info("Ruta Multi-DEX detectada. Usando execute_multi_dex_route.", extra=log_extra)
                await self.executor.execute_multi_dex_route(
                    route_detailed=route_detailed,
                    initial_amount_in=amount_in_wei,
                    gross_profit_usdt=gross_profit_usdt,
                    correlation_id=correlation_id
                )
            
            return opportunity
        return None

    async def _get_amount_out(self, amount_in: float, token_in_sym: str, token_out_sym: str, pools: Dict, dex_to_use: Optional[str] = None) -> Optional[float]:
        try:
            token_in_addr, token_out_addr = self.fetcher.tokens_by_symbol[token_in_sym], self.fetcher.tokens_by_symbol[token_out_sym]
            pool_key = tuple(sorted((token_in_addr, token_out_addr)))
            
            pool_data = None
            if dex_to_use:
                for key, data in pools.items():
                    if key == pool_key and data.get('dex_name') == dex_to_use:
                        pool_data = data
                        break
            else:
                pool_data = pools.get(pool_key)

            if not pool_data: return None

            reserves, dex_fee, pool_token0 = pool_data.get('reserves'), pool_data.get('fee', 0.0025), pool_data.get('token0')
            if not all([reserves, dex_fee is not None, pool_token0]): return None
            
            (reserve_in_raw, reserve_out_raw) = (reserves[0], reserves[1]) if pool_token0 == token_in_addr else (reserves[1], reserves[0])
            
            decimals_in = await self.fetcher._get_token_decimals(token_in_addr)
            decimals_out = await self.fetcher._get_token_decimals(token_out_addr)
            amount_in_wei = int(amount_in * (10**decimals_in))
            if reserve_in_raw == 0: return 0.0
            
            amount_in_with_fee = amount_in_wei * int((1 - dex_fee) * 10000) // 10000
            numerator = amount_in_with_fee * reserve_out_raw
            denominator = reserve_in_raw + amount_in_with_fee
            if denominator == 0: return 0.0
            
            amount_out_wei = numerator // denominator
            return float(amount_out_wei) / (10**decimals_out)
        except Exception as e:
            self.logger.error(f"Error en _get_amount_out para {token_in_sym}->{token_out_sym}: {e}", exc_info=True)
            return None

    async def _get_bnb_price(self, pools: Dict) -> Optional[float]:
        if not all(s in self.fetcher.tokens_by_symbol for s in ["USDT", "WBNB"]): return None
        return await self._get_amount_out(1.0, "WBNB", "USDT", pools)


class StablecoinArbitrage:
    def __init__(self, fetcher: "BSCDataFetcher", settings_manager: "SettingsManager"):
        self.fetcher = fetcher
        self.settings_manager = settings_manager
        self.logger = ArbitriumLogger('stablecoin_arb', 'arbitrium.log')
        self.min_profit_percentage = self.settings_manager.get_setting("MIN_STABLECOIN_PROFIT_PERCENTAGE", 0.05)
        self.stablecoin_pairs: List[Tuple[str, str]] = self._generate_stablecoin_pairs()

    def _generate_stablecoin_pairs(self) -> List[Tuple[str, str]]:
        stablecoins = self.fetcher.get_stablecoins()
        return list(combinations(stablecoins, 2)) if len(stablecoins) >= 2 else []

    #La función es asíncrona para permitir 'await'
    async def find_opportunities(self) -> Sequence[StablecoinOpportunity]:
        opportunities: List[StablecoinOpportunity] = []
        if not self.stablecoin_pairs: return opportunities
        self.logger.debug(f"Buscando oportunidades de arbitraje en {len(self.stablecoin_pairs)} pares de stablecoins.")

        for stable_a, stable_b in self.stablecoin_pairs:
            #Se añade 'await' para esperar la obtención de precios
            prices_a_to_b = await self.fetcher.get_prices_for_pair_across_dexs(stable_a, stable_b)
            prices_b_to_a = await self.fetcher.get_prices_for_pair_across_dexs(stable_b, stable_a)

            if not prices_a_to_b or not prices_b_to_a: continue

            best_buy_dex, best_buy_price = max(prices_a_to_b.items(), key=lambda item: item[1] or -1.0)
            best_sell_dex, best_sell_price = max(prices_b_to_a.items(), key=lambda item: item[1] or -1.0)

            if best_buy_price and best_sell_price and best_buy_dex != best_sell_dex:
                profit_percentage = ((best_buy_price * best_sell_price) - 1.0) * 100
                if profit_percentage > self.min_profit_percentage:
                    opportunities.append(StablecoinOpportunity(
                        pair=(stable_a, stable_b), route=[stable_a, stable_b, stable_a],
                        buy_at=best_buy_dex, sell_at=best_sell_dex, buy_price=best_buy_price,
                        sell_price=best_sell_price, profit_percent=round(profit_percentage, 4),
                        timestamp=int(time.time()), opportunity_type="Stablecoin"
                    ))
                    self.logger.info(f"¡Oportunidad Stablecoin! {stable_a}/{stable_b}, Ganancia: {profit_percentage:.4f}%")
        return opportunities

__all__ = ['CombinedArbitrage', 'CrossDEXArbitrage', 'MultiStepArbitrage', 'StablecoinArbitrage']