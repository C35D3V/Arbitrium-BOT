import asyncio
import json
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional

import websockets
from web3 import Web3

from core.database import DatabaseManager
from core.logger import ArbitriumLogger

SYNC_EVENT_TOPIC = "0x1c411e9a96e0712632c63b49b663aca6d4cb185b3b6ce80c2e0dc3ee4de1239c"

class PoolMonitor:
    """
    Gestiona el estado de los pools de liquidez en tiempo real usando una conexión
    directa de websockets para mayor fiabilidad.
    """
    def __init__(self, wss_url: str, data_fetcher, db_manager: DatabaseManager,
                 event_callback: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None):
        self.logger = ArbitriumLogger('pool_monitor', 'arbitrium.log')
        self.wss_url = wss_url
        self.fetcher = data_fetcher
        self.db_manager = db_manager
        self.event_callback = event_callback
        
        self.pools_info: Dict[str, Dict[str, Any]] = {}
        self.live_reserves: Dict[str, tuple] = {}
        
        self._lock = asyncio.Lock()
        self.running = False
        self._task: Optional[asyncio.Task] = None

    @classmethod
    def create(cls, data_fetcher, db_manager: DatabaseManager,
               event_callback: Optional[Callable[[str], Coroutine[Any, Any, None]]] = None) -> Optional["PoolMonitor"]:
        if not data_fetcher.wss_url:
            return None
        monitor = cls(data_fetcher.wss_url, data_fetcher, db_manager, event_callback)
        # La inicialización de datos ahora es síncrona
        monitor._initial_state_fetch()
        return monitor

    def _initial_state_fetch(self):
        self.logger.info("Iniciando la obtención del estado inicial de los pools...")
        cached_pairs = self.db_manager.get_all_discovered_pairs()
        all_pools_data: List[Dict[str, Any]] = []
        if cached_pairs:
            self.logger.info(f"Cargando {len(cached_pairs)} pares desde la base de datos local.")
            pair_abi = self.fetcher.get_abi("pair")
            if not pair_abi:
                self.logger.error("No se pudo obtener el ABI de par para obtener reservas. Abortando.")
                return
            w3_sync = self.fetcher.w3 
            for pair_info in cached_pairs:
                try:
                    pair_contract = w3_sync.eth.contract(address=pair_info['pair_address'], abi=pair_abi)
                    reserves = pair_contract.functions.getReserves().call()
                    pair_info['reserves'] = (reserves[0], reserves[1])
                    all_pools_data.append(pair_info)
                except Exception as e:
                    self.logger.warning(f"No se pudieron obtener las reservas para el par cacheado {pair_info['pair_address']}: {e}")
        else:
            self.logger.info("Base de datos de pares vacía. Buscando en la blockchain...")
            discovered_pools = {}
            for dex_name in self.fetcher.dex_routers.keys():
                dex_pools = self.fetcher.get_all_dex_liquidity_pools(dex_name)
                if dex_pools:
                    discovered_pools.update(dex_pools)
            if discovered_pools:
                pools_to_save = list(discovered_pools.values())
                self.db_manager.add_discovered_pairs(pools_to_save)
                all_pools_data = pools_to_save
        if not all_pools_data:
            self.logger.error("No se pudo obtener el estado inicial de ningún pool.")
            return
        for pool_data in all_pools_data:
            pair_address = Web3.to_checksum_address(pool_data['pair_address'])
            self.pools_info[pair_address] = {
                'token0': pool_data['token0_address'],
                'token1': pool_data['token1_address'],
                'fee': pool_data.get('fee', 0.0025),
                'dex_name': pool_data.get('dex_name', 'Unknown')
            }
            if 'reserves' in pool_data:
                self.live_reserves[pair_address] = pool_data['reserves']
        self.logger.info(f"Estado inicial obtenido. Monitoreando {len(self.live_reserves)} pools.")

    def _handle_event(self, event_data: Dict[str, Any]):
        try:
            # La estructura del evento JSON-RPC es diferente
            if event_data.get("method") != "eth_subscription":
                return
            
            result = event_data.get("params", {}).get("result", {})
            if not result:
                return

            pair_address = Web3.to_checksum_address(result['address'])
            data_hex = result.get('data', '0x')
            
            if data_hex.startswith('0x'):
                data_hex = data_hex[2:]

            reserve0 = int(data_hex[0:64], 16)
            reserve1 = int(data_hex[64:128], 16)

            if pair_address in self.live_reserves:
                self.live_reserves[pair_address] = (reserve0, reserve1)
                
                if self.event_callback:
                    asyncio.create_task(self.event_callback(pair_address))

        except Exception as e:
            self.logger.error(f"Error procesando evento Sync: {e} | Evento: {event_data}")

    async def _event_loop(self):
        self.logger.info("Iniciando bucle de escucha de eventos 'Sync' con la librería 'websockets'.")
        pair_addresses = list(self.pools_info.keys())
        
        subscription_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_subscribe",
            "params": [
                "logs",
                {
                    "address": pair_addresses,
                    "topics": [SYNC_EVENT_TOPIC]
                }
            ]
        }

        while self.running:
            try:
                async with websockets.connect(self.wss_url) as websocket:
                    await websocket.send(json.dumps(subscription_request))
                    subscription_response = await websocket.recv()
                    self.logger.info(f"Suscripción a eventos 'Sync' establecida: {subscription_response}")
                    
                    while self.running:
                        message = await websocket.recv()
                        event_data = json.loads(message)
                        self._handle_event(event_data)
                        
            except (websockets.exceptions.ConnectionClosed, ConnectionRefusedError) as e:
                self.logger.error(f"Conexión WebSocket cerrada: {e}. Reconectando en 10 segundos...")
            except Exception as e:
                self.logger.error(f"Error inesperado en el bucle de eventos: {e}", exc_info=True)
            
            if self.running:
                await asyncio.sleep(10)

    def start(self):
        if self.running:
            self.logger.warning("El monitor ya está en ejecución.")
            return
        if not self.pools_info:
            self.logger.error("No hay pools para monitorear. La tarea no se iniciará.")
            return
        self.running = True
        self._task = asyncio.create_task(self._event_loop())
        self.logger.info("PoolMonitor iniciado en una tarea de fondo de asyncio.")

    def stop(self):
        self.logger.info("Deteniendo PoolMonitor...")
        self.running = False
        if self._task:
            self._task.cancel()

    async def get_all_live_pools(self) -> Dict[tuple, Dict[str, Any]]:
        async with self._lock:
            live_reserves_copy = self.live_reserves.copy()
            pools_info_copy = self.pools_info.copy()
        formatted_pools = {}
        for pair_addr, reserves in live_reserves_copy.items():
            if pair_addr in pools_info_copy:
                info = pools_info_copy[pair_addr]
                token0 = info.get('token0')
                token1 = info.get('token1')
                if token0 and token1:
                    pool_key = tuple(sorted((token0, token1)))
                    # Al devolver los pools en vivo, volvemos a usar las claves 'token0' y 'token1'
                    # para mantener la consistencia en el resto de la aplicación.
                    formatted_pools[pool_key] = {
                        'pair_address': pair_addr,
                        'reserves': reserves,
                        'fee': info['fee'],
                        'token0': token0,
                        'token1': token1,
                        'dex_name': info['dex_name']
                    }
        return formatted_pools