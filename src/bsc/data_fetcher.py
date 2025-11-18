import os
import json
import requests
import aiohttp
from aiohttp import ClientTimeout 
from web3 import Web3
from web3.exceptions import ContractLogicError
from web3.middleware import ExtraDataToPOAMiddleware
from pathlib import Path
from dataclasses import dataclass
from typing import Tuple, Optional, List, Dict, Any
import logging
import time
import asyncio
from core.logger import ArbitriumLogger
from core.config_validator import validate_bsc_config
from core.cache import cache_data
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

# Cargar las variables de entorno al inicio
load_dotenv()

logger = ArbitriumLogger('data_fetcher', 'arbitrium.log')
config_path = Path(__file__).parent.parent.parent / "config" / "bsc_config.json"

try:
    config = validate_bsc_config(config_path)
    logger.info("Configuración local (JSON) validada correctamente")
except Exception as e:
    logger.critical(f"Error en configuración BSC: {str(e)}")
    raise SystemExit(1)

@dataclass
class TokenInfo:
    symbol: str
    address: str
    decimals: int = 18

class BSCDataFetcher:
    def __init__(self):
        self.logger = logger
        self.config = config
        
        self.rpc_url = os.getenv("RPC_URL")
        self.wss_url = os.getenv("WSS_URL")
        self.alchemy_api_key = os.getenv("ALCHEMY_API_KEY")
        self.alchemy_api_url = os.getenv("ALCHEMY_PRICES_API_URL", "https://api.g.alchemy.com/prices/v1/")
        self.bsc_gas_api_url = os.getenv("BSCGAS_API_URL", "https://bscgas.info/gas")

        if not self.rpc_url:
            raise ValueError("La variable de entorno 'RPC_URL' no está definida. Revisa tu archivo .env")

        self._connect()

        if not self.wss_url:
            self.logger.warning("La variable de entorno 'WSS_URL' no se encuentra. El PoolMonitor no podrá funcionar.")
        
        self.dex_routers = {name: details['router'] for name, details in self.config['dexes'].items()}
        self.dex_factories = {name: details['factory'] for name, details in self.config['dexes'].items()}
        self.dex_fees = {name: details['fee'] for name, details in self.config['dexes'].items()}
        self.abis = self.config['abis']
        
        # Estandariza todas las direcciones a formato checksum al iniciar
        self.tokens_by_symbol: Dict[str, str] = {
            symbol: Web3.to_checksum_address(addr)
            for symbol, addr in self.config['tokens'].items()
        }
        self.symbols_by_address: Dict[str, str] = {
            addr: symbol for symbol, addr in self.tokens_by_symbol.items()
        }
        
        self.lending_platforms: Dict[str, Dict[str, Any]] = self.config.get('lending_platforms', {})
        self.price_oracle_address = self.config.get('price_oracle_address')

        self.known_decimals: Dict[str, int] = {
            "USDT": 18, "BUSD": 18, "WBNB": 18, "ETH": 18, "BTCB": 18,
            "DAI": 18, "USDC": 18, "CAKE": 18, "XVS": 18, "ALPACA": 18, "AAVE": 18,
        }

    def _connect(self):
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={'timeout': 15}))
        self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        if self.w3.is_connected():
            self.logger.info("Conectado exitosamente al RPC.")
        else:
            self.logger.error("No se pudo establecer una conexión con el RPC.")
            raise ConnectionError("Fallo en la conexión RPC.")

    @cache_data(ttl=300)
    def get_all_dex_liquidity_pools(self, dex_name: str) -> Dict[tuple, Dict[str, Any]]:
        self.logger.debug(f"Iniciando obtención de pools de liquidez para {dex_name}...")
        factory_address = self.dex_factories.get(dex_name)
        factory_abi = self.get_abi("factory")
        pair_abi = self.get_abi("pair")

        if not factory_address or not factory_abi or not pair_abi:
            self.logger.error(f"Configuración de factory o ABI faltante para {dex_name}.")
            return {}

        factory_contract = self.w3.eth.contract(address=Web3.to_checksum_address(factory_address), abi=factory_abi)
        
        token_addresses = list(self.tokens_by_symbol.values())
        pools = {}

        for i in range(len(token_addresses)):
            for j in range(i + 1, len(token_addresses)):
                token_a_addr = Web3.to_checksum_address(token_addresses[i])
                token_b_addr = Web3.to_checksum_address(token_addresses[j])

                try:
                    pair_address = factory_contract.functions.getPair(token_a_addr, token_b_addr).call()
                    
                    if pair_address != '0x0000000000000000000000000000000000000000':
                        pair_contract = self.w3.eth.contract(address=pair_address, abi=pair_abi)
                        reserves = pair_contract.functions.getReserves().call()
                        token0 = pair_contract.functions.token0().call()
                        
                        pool_key = tuple(sorted((token_a_addr, token_b_addr)))
                        
                        token0_in_pool, token1_in_pool = (pool_key[0], pool_key[1]) if token0 == pool_key[0] else (pool_key[1], pool_key[0])
                        
                        pools[pool_key] = {
                            'pair_address': pair_address,
                            'reserves': (reserves[0], reserves[1]) if token0 == token0_in_pool else (reserves[1], reserves[0]),
                            'fee': self.dex_fees.get(dex_name, 0.0025),
                            'token0': token0_in_pool,
                            'token1': token1_in_pool,
                            'dex_name': dex_name
                        }
                except ContractLogicError:
                    continue
                except Exception as e:
                    self.logger.warning(f"Error obteniendo par {token_a_addr}-{token_b_addr} en {dex_name}: {e}")

        self.logger.info(f"Se encontraron {len(pools)} pools de liquidez con reservas en {dex_name}.")
        return pools

    def get_gas_price(self) -> Optional[int]:
        try:
            return self.w3.eth.gas_price
        except Exception as e:
            self.logger.error(f"No se pudo obtener el precio del gas: {e}")
            return None

    @cache_data(ttl=15)
    async def get_advanced_gas_prices(self) -> Optional[Dict[str, Any]]:
        if not self.bsc_gas_api_url:
            self.logger.warning("BSCGAS_API_URL no configurada.")
            return None
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.bsc_gas_api_url, timeout=ClientTimeout(total=5)) as response:
                    response.raise_for_status()
                    data = await response.json()
            
            if all(key in data for key in ['slow', 'standard', 'fast']):
                self.logger.debug(f"Precios de gas avanzados obtenidos: {data}")
                return {key: data[key] for key in ['slow', 'standard', 'fast', 'instant'] if key in data}
            else:
                self.logger.error(f"Formato de respuesta inesperado de la API de gas: {data}")
                return None
        except Exception as e:
            self.logger.error(f"Error al obtener precios de gas avanzados de {self.bsc_gas_api_url}: {e}", exc_info=True)
            return None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(2),
        retry=retry_if_exception_type((requests.exceptions.ConnectionError, ContractLogicError, Exception))
    )
    @cache_data(ttl=3600)
    async def _get_token_decimals(self, token_address: str) -> int:
        """
        Obtiene los decimales de un token de forma asíncrona.
        Esta función es ahora explícitamente una corutina para evitar ambigüedades
        con los decoradores, solucionando problemas de análisis estático.
        """
        try:
            checksum_address = Web3.to_checksum_address(token_address)
            erc20_abi = self.get_abi("erc20")
            if not erc20_abi:
                raise ValueError("ABI ERC20 no encontrado")

            token_contract = self.w3.eth.contract(address=checksum_address, abi=erc20_abi)
            
            # La llamada .call() es bloqueante, pero al estar en una función async,
            # se integra correctamente en el bucle de eventos de asyncio.
            decimals = token_contract.functions.decimals().call()
            self.logger.debug(f"Decimales para {checksum_address}: {decimals} (obtenido on-chain)")
            return decimals
        except Exception as e:
            checksum_address = Web3.to_checksum_address(token_address)
            token_symbol = self.symbols_by_address.get(checksum_address)
            
            if token_symbol and token_symbol in self.known_decimals:
                hardcoded_decimals = self.known_decimals[token_symbol]
                self.logger.warning(
                    f"No se pudo obtener decimales de la blockchain para {token_symbol} ({checksum_address}) tras reintentos. "
                    f"Usando valor conocido: {hardcoded_decimals}. Error original: {e}"
                )
                return hardcoded_decimals
            else:
                self.logger.error(
                    f"Error final al obtener decimales para {checksum_address}. Asumiendo 18 por defecto.",
                    exc_info=True
                )
                return 18

    @cache_data(ttl=8)
    async def get_price_route(self, dex_name: str, path_symbols: List[str], initial_normalized_amount: float = 1.0) -> Optional[float]:
        if len(path_symbols) < 2: return None
        
        try:
            token_in_address = self.tokens_by_symbol[path_symbols[0]]
            #Añadimos 'await' para obtener el valor numérico
            decimals_in = await self._get_token_decimals(token_in_address)
            amount_in_wei = int(initial_normalized_amount * (10**decimals_in))
            path_addresses = [self.tokens_by_symbol[s] for s in path_symbols]
            
            router_address = self.dex_routers.get(dex_name)
            router_abi = self.get_abi("router")

            if not router_address or not router_abi:
                return None
        except Exception as e:
            self.logger.error(f"Error preparando la llamada a get_price_route: {e}")
            return None

        try:
            router_contract = self.w3.eth.contract(address=router_address, abi=router_abi)
            amounts_out = router_contract.functions.getAmountsOut(amount_in_wei, path_addresses).call()
            
            token_out_address = self.tokens_by_symbol[path_symbols[-1]]
            #Añadimos 'await' para obtener el valor numérico
            decimals_out = await self._get_token_decimals(token_out_address)
            
            final_amount_normalized = amounts_out[-1] / (10**decimals_out)
            return final_amount_normalized
        except ContractLogicError:
            self.logger.debug(f"La ruta {'→'.join(path_symbols)} en {dex_name} ha revertido (sin liquidez).")
            return None
        except Exception as e:
            self.logger.error(f"Error de conexión en get_price_route para {path_symbols} en {dex_name}: {e}")
            return None
            
    @cache_data(ttl=10)
    async def get_prices_for_pair_across_dexs(self, token_in_symbol: str, token_out_symbol: str, amount_in: float = 1.0) -> Dict[str, float]:
        """
        Obtiene los precios de un par de tokens a través de todos los DEXes configurados,
        ejecutando las consultas de forma concurrente para mayor eficiencia.
        """
        self.logger.debug(f"Consultando precios para el par {token_in_symbol}/{token_out_symbol} en todos los DEXs de forma concurrente.")
        
        tasks = []
        dex_names = list(self.dex_routers.keys())

        # Creamos una lista de tareas. Como get_price_route está decorada con @cache_data,
        # ya devuelve una corrutina que podemos añadir directamente a la lista.
        for dex_name in dex_names:
            tasks.append(
                self.get_price_route(dex_name, [token_in_symbol, token_out_symbol], amount_in)
            )

        # Usamos asyncio.gather para ejecutar todas las corrutinas en paralelo.
        # return_exceptions=True evita que una consulta fallida detenga todas las demás.
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        prices: Dict[str, float] = {}
        for dex_name, price_result in zip(dex_names, results):
            # Nos aseguramos de que la tarea no haya fallado y el precio sea un número válido.
            if not isinstance(price_result, Exception) and isinstance(price_result, (int, float)) and price_result > 0:
                prices[dex_name] = price_result
            elif isinstance(price_result, Exception):
                self.logger.warning(f"La consulta de precio para {dex_name} falló durante la ejecución concurrente: {price_result}")

        self.logger.debug(f"Precios obtenidos para {token_in_symbol}/{token_out_symbol}: {prices}")
        return prices

    @cache_data(ttl=60)
    async def get_current_prices(self, quote_token_symbol: str = "USDT") -> Dict[str, float]:
        self.logger.info("Obteniendo precios actuales desde la API de Alchemy.")
        all_token_symbols = list(self.tokens_by_symbol.keys())
        
        prices_in_usd = await self._get_prices_from_alchemy(all_token_symbols, "USD")

        if not prices_in_usd:
            self.logger.warning("No se pudieron obtener precios de Alchemy.")
            return {}

        quote_price_in_usd = prices_in_usd.get(quote_token_symbol)
        if not quote_price_in_usd or quote_price_in_usd == 0:
            self.logger.error(f"No se pudo obtener el precio del token de cotización '{quote_token_symbol}'.")
            return prices_in_usd

        return {symbol: price_usd / quote_price_in_usd for symbol, price_usd in prices_in_usd.items() if price_usd is not None}
    
    async def _get_prices_from_alchemy(self, token_symbols: List[str], base_currency: str = "USD") -> Dict[str, float]:
        if not self.alchemy_api_key or not self.alchemy_api_url:
            self.logger.error("La clave de API o la URL de Alchemy no están configuradas.")
            return {}

        endpoint = f"{self.alchemy_api_url}{self.alchemy_api_key}/tokens/by-symbol"
        all_prices: Dict[str, float] = {}
        
        chunk_size = 20
        token_chunks = [token_symbols[i:i + chunk_size] for i in range(0, len(token_symbols), chunk_size)]
        
        async with aiohttp.ClientSession() as session:
            for i, chunk in enumerate(token_chunks):
                params = {'symbols': chunk, 'currency': base_currency}
                self.logger.debug(f"Consultando lote {i+1}/{len(token_chunks)} de precios a Alchemy.")
                
                try:
                    async with session.get(endpoint, params=params, headers={'accept': 'application/json'}, timeout=ClientTimeout(total=10)) as response:
                        response.raise_for_status()
                        data = await response.json()
                    
                    for item in data.get('data', []):
                        symbol = item.get('symbol')
                        # Se obtiene la lista de precios y se verifica que no esté vacía
                        prices_list = item.get('prices', [])
                        if symbol and prices_list:
                            price_value = prices_list[0].get('value')
                            if price_value is not None:
                                all_prices[symbol] = float(price_value)
                except Exception as e:
                    self.logger.error(f"Error al contactar la API de Alchemy para el lote {i+1}: {e}")
                    continue
        
        return all_prices

    def get_stablecoins(self) -> List[str]:
        return [s for s in self.tokens_by_symbol if "USD" in s.upper() or s == "DAI"]

    def get_abi(self, abi_type: str) -> Optional[List[Dict[str, Any]]]:
        abi = self.abis.get(abi_type)
        if not abi:
            self.logger.error(f"ABI '{abi_type}' no encontrada en la configuración.")
        return abi