import os
import time
import json 
import asyncio
from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import (
    TransactionNotFound,
    ContractLogicError,
    TimeExhausted
)
from web3.middleware import ExtraDataToPOAMiddleware
from eth_utils.exceptions import ValidationError
import requests
from core.logger import ArbitriumLogger
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
import logging
import threading
from typing import List, Dict, Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from bsc.data_fetcher import BSCDataFetcher
    from core.settings_manager import SettingsManager
    from core.notifications import NotificationManager

from web3.types import TxParams, Wei, Nonce
from core.database import DatabaseManager

# Cargar variables de entorno al inicio del script
load_dotenv()

logger = ArbitriumLogger('bsc_execution', 'arbitrium.log')

class NonceManager:
    def __init__(self, w3: Web3, account_address: str):
        self.w3 = w3
        self.account_address = w3.to_checksum_address(account_address)
        self._nonce_lock = threading.Lock()
        self._current_nonce = None
        self.logger = logger

    def _fetch_latest_nonce(self) -> int:
        try:
            latest_nonce = self.w3.eth.get_transaction_count(self.account_address)
            self.logger.debug(f"Nonce fresco obtenido de la blockchain para {self.account_address}: {latest_nonce}")
            return latest_nonce
        except Exception as e:
            self.logger.error(f"Error al obtener el nonce más reciente de la blockchain: {e}", exc_info=True)
            raise

    def get_nonce(self) -> Nonce:
        with self._nonce_lock:
            latest_on_chain_nonce = self._fetch_latest_nonce()
            if self._current_nonce is None or self._current_nonce < latest_on_chain_nonce:
                self._current_nonce = latest_on_chain_nonce

            nonce_to_use = self._current_nonce
            self._current_nonce += 1
            self.logger.debug(f"Asignando nonce: {nonce_to_use}. Próximo nonce en cola: {self._current_nonce}")
            return Nonce(nonce_to_use)

    def reset_nonce(self):
        with self._nonce_lock:
            self.logger.warning("Reseteando nonce: forzando la obtención del nonce más reciente de la blockchain.")
            self._current_nonce = self._fetch_latest_nonce()


class BSCTransactionExecutor:
    ERC20_ABI = [{
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    }]

    def __init__(self, w3: Web3, private_key: str, db_manager: DatabaseManager,
                 fork_rpc_url: Optional[str] = None, data_fetcher: Optional["BSCDataFetcher"] = None,
                 settings_manager: Optional["SettingsManager"] = None,
                 notification_manager: Optional["NotificationManager"] = None):
        if not private_key or not isinstance(private_key, str):
            raise ValueError("Se requiere una clave privada válida (string)")

        if not private_key.startswith('0x') or len(private_key) != 66:
            raise ValueError("Formato de clave privada inválido: debe comenzar con '0x' y tener 66 caracteres.")

        self.w3 = w3
        self.account = self.w3.eth.account.from_key(private_key)
        self.db_manager = db_manager
        self.nonce_manager = NonceManager(w3, self.account.address)
        self.logger = logger

        self.fork_rpc_url = fork_rpc_url
        if not self.fork_rpc_url:
            self.logger.warning("No se proporcionó FORK_RPC_URL. Las transacciones no serán simuladas.")

        self.data_fetcher = data_fetcher
        if not self.data_fetcher:
            self.logger.warning("BSCTransactionExecutor inicializado sin DataFetcher. No se podrá calcular el costo de gas en USDT post-ejecución.")

        self.settings_manager = settings_manager
        if not self.settings_manager:
            self.logger.warning("BSCTransactionExecutor inicializado sin SettingsManager. Se usarán valores por defecto para algunas configuraciones.")

        self.notification_manager = notification_manager
        self.logger.info(f"Executor inicializado para la dirección: {self.account.address}")

    def get_token_balance(self, token_address: str) -> int:
        token_address = self.w3.to_checksum_address(token_address)
        token_contract = self.w3.eth.contract(address=token_address, abi=self.ERC20_ABI)
        try:
            balance = token_contract.functions.balanceOf(self.account.address).call()
            self.logger.info(f"Balance de {token_address} para {self.account.address}: {balance}")
            return balance
        except Exception as e:
            self.logger.error(f"Error al obtener el balance del token {token_address}: {e}", exc_info=True)
            return 0

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ConnectionError, requests.exceptions.ConnectionError)),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    async def get_gas_price_strategy(self, urgency: str = 'standard') -> Wei:
        if self.settings_manager and self.settings_manager.get_setting("USE_ADVANCED_GAS_API", True):
            if self.data_fetcher:
                advanced_gas_prices = await self.data_fetcher.get_advanced_gas_prices()
                if advanced_gas_prices:
                    try:
                        price_key = {'fast': 'fast', 'standard': 'standard'}.get(urgency, 'slow')
                        gas_price_gwei = advanced_gas_prices.get(price_key, advanced_gas_prices['standard'])
                        buffered_gas_price = self.w3.to_wei(gas_price_gwei, 'gwei')
                        self.logger.debug(
                            f"Gas Price Avanzado para '{urgency}': {gas_price_gwei:.2f} Gwei "
                            f"(en Wei: {buffered_gas_price})."
                        )
                        return buffered_gas_price
                    except Exception as e:
                        self.logger.warning(f"Error al usar precios de gas avanzados: {e}. Revertiendo a estrategia base.", exc_info=True)
            else:
                self.logger.warning("DataFetcher no disponible para precios de gas avanzados. Revertiendo a estrategia base.")

        try:
            multiplier_standard = self.settings_manager.get_setting("GAS_PRICE_MULTIPLIER_STANDARD", 1.05) if self.settings_manager else 1.05
            multiplier_fast = self.settings_manager.get_setting("GAS_PRICE_MULTIPLIER_FAST", 1.20) if self.settings_manager else 1.20

            multiplier = multiplier_fast if urgency == 'fast' else multiplier_standard
            current_gas_price = self.w3.eth.gas_price
            buffered_gas_price = Wei(int(current_gas_price * multiplier))
            
            self.logger.debug(
                f"Gas Price actual (fallback): {current_gas_price / (10**9):.2f} Gwei. "
                f"Usando estrategia '{urgency}': {buffered_gas_price / (10**9):.2f} Gwei."
            )
            return buffered_gas_price
        except Exception as e:
            self.logger.error(f"Error inesperado al obtener el gas price: {e}. Usando 5 Gwei por defecto.", exc_info=True)
            return self.w3.to_wei(5, 'gwei')

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((ConnectionError, requests.exceptions.ConnectionError, TransactionNotFound)),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    def estimate_transaction_gas(self, transaction: TxParams) -> int:
        try:
            estimated_gas = self.w3.eth.estimate_gas(transaction)
            buffered_estimated_gas = int(estimated_gas * 1.25)
            self.logger.info(f"Gas estimado: {estimated_gas}. Usando Gas con buffer: {buffered_estimated_gas}.")
            return buffered_estimated_gas
        except ContractLogicError as e:
            self.logger.warning(f"La estimación de gas indica que la transacción revertiría: {e}. Descartando swap.")
            return 0
        except (ConnectionError, requests.exceptions.ConnectionError, TransactionNotFound) as e:
            self.logger.warning(f"Error de conexión o red al estimar gas (reintentando): {e}", exc_info=True)
            raise
        except Exception as e:
            self.logger.error(f"Error inesperado al estimar gas: {e}. Usando gas por defecto.", exc_info=True)
            return 250000

    async def simulate_swap(self, unsigned_tx: TxParams, correlation_id: Optional[str] = None) -> bool:
        log_extra = {'correlation_id': correlation_id}

        if not self.fork_rpc_url:
            self.logger.warning("Simulación omitida: no hay FORK_RPC_URL configurada.", extra=log_extra)
            return True

        self.logger.info("--- Iniciando Simulación de Transacción en Fork ---", extra=log_extra)
        try:
            w3_fork = Web3(Web3.HTTPProvider(self.fork_rpc_url))
            w3_fork.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

            signed_tx = self.w3.eth.account.sign_transaction(unsigned_tx, private_key=self.account.key)
            tx_hash_fork = w3_fork.eth.send_raw_transaction(signed_tx.rawTransaction)
            self.logger.debug(f"Transacción enviada al fork. Hash de simulación: {tx_hash_fork.hex()}", extra=log_extra)

            tx_timeout = self.settings_manager.get_setting("TX_TIMEOUT_SECONDS", 300) if self.settings_manager else 300
            receipt = w3_fork.eth.wait_for_transaction_receipt(tx_hash_fork, timeout=tx_timeout)

            if receipt['status'] == 1:
                self.logger.info("✅ Simulación EXITOSA.", extra=log_extra)
                return True
            else:
                self.logger.warning(f"❌ Simulación FALLIDA. La transacción revertiría en la mainnet. Recibo: {receipt}", extra=log_extra)
                return False

        except ContractLogicError as e:
            self.logger.error(f"❌ Simulación FALLIDA. Error de lógica del contrato: {e}", extra=log_extra)
            return False
        except Exception as e:
            self.logger.error(f"❌ Simulación FALLIDA. Error inesperado durante la simulación.", extra=log_extra, exc_info=True)
            return False

    async def execute_swap(self,
                           router_address: str,
                           router_abi: List[Dict[str, Any]],
                           amount_in: int,
                           amount_out_min: int,
                           path: List[str],
                           to_address: str,
                           deadline: int,
                           dex_name: str,
                           gross_profit_usdt: Optional[float] = None,
                           correlation_id: Optional[str] = None) -> Optional[str]:

        log_extra = {'correlation_id': correlation_id}
        
        # Obtenemos los símbolos para el registro antes de cualquier otra cosa
        path_symbols = []
        if self.data_fetcher:
            path_symbols = [self.data_fetcher.symbols_by_address.get(addr, addr[:6]) for addr in path]
        else:
            path_symbols = [addr[:6] for addr in path]

        self.logger.info(f"Preparando swap en {dex_name}: {amount_in} de {path_symbols[0]} para obtener {path_symbols[-1]} (min {amount_out_min})", extra=log_extra)

        trade_data = {
            'timestamp': int(time.time()),
            'route': " → ".join(path_symbols),
            'tx_hash': None,
            'status': 'failed', # Se actualiza en caso de éxito o fallo específico
            'correlation_id': correlation_id,
            'gross_profit_usdt': gross_profit_usdt,
            'net_profit_usdt': None,
            'gas_cost_usdt': None,
            'price_snapshot': None
        }
        
        # --- INICIO NUEVA LÓGICA DE SNAPSHOT ---
        price_snapshot = {}
        if self.data_fetcher:
            try:
                current_prices = await self.data_fetcher.get_current_prices()
                for symbol in path_symbols:
                    if symbol and symbol in current_prices:
                        price_snapshot[symbol] = current_prices[symbol]
                # Añadimos el precio del gas (BNB) al snapshot para contexto
                if 'WBNB' in current_prices:
                    price_snapshot['BNB_price_usdt'] = current_prices['WBNB']

            except Exception as e:
                self.logger.error(f"No se pudo crear el snapshot de precios: {e}", extra=log_extra)
        
        trade_data['price_snapshot'] = json.dumps(price_snapshot)

        try:
            router_contract = self.w3.eth.contract(address=self.w3.to_checksum_address(router_address), abi=router_abi)
            gas_price_strategy = await self.get_gas_price_strategy()
            current_nonce = self.nonce_manager.get_nonce()

            tx_params: TxParams = {
                'from': self.account.address,
                'nonce': current_nonce,
                'gasPrice': gas_price_strategy,
                'value': Wei(0)
            }
            
            # Las direcciones en el path para la transacción deben ser checksummed
            path_checksummed = [self.w3.to_checksum_address(addr) for addr in path]

            unsigned_tx = router_contract.functions.swapExactTokensForTokens(
                amount_in, amount_out_min, path_checksummed, self.w3.to_checksum_address(to_address), deadline
            ).build_transaction(tx_params)

            estimated_gas = self.estimate_transaction_gas(unsigned_tx)
            if estimated_gas == 0:
                self.logger.warning(f"Swap en {dex_name} descartado (estimación de gas indica reversión).", extra=log_extra)
                trade_data['status'] = 'failed_gas_estimation'
                self.db_manager.add_executed_trade(trade_data)
                if self.notification_manager:
                    await self.notification_manager.send_trade_failure_notification(trade_data)
                return None
            unsigned_tx['gas'] = estimated_gas

            is_simulation_successful = await self.simulate_swap(unsigned_tx, correlation_id)

            # --- LÓGICA DE PAPER TRADING ---
            paper_trading_enabled = self.settings_manager.get_setting("PAPER_TRADING_MODE", True) if self.settings_manager else True

            if paper_trading_enabled:
                if is_simulation_successful:
                    self.logger.info(f"📰 MODO PAPER TRADING: La simulación para {dex_name} fue EXITOSA. La transacción NO se enviará a la mainnet.", extra=log_extra)
                    trade_data['status'] = 'simulated_success'
                    self.db_manager.add_executed_trade(trade_data)
                    # Opcional: Enviar una notificación específica de paper trading
                    if self.notification_manager:
                        await self.notification_manager.send_message(f"📰 <b>Paper Trade Exitoso (Simulado)</b>\nRuta: {trade_data['route']}")
                    # Retornamos un hash falso para indicar que la simulación fue bien
                    return f"simulated_{correlation_id}"
                else:
                    self.logger.error(f"📰 MODO PAPER TRADING: La simulación para {dex_name} FALLÓ. La transacción se habría revertido.", extra=log_extra)
                    trade_data['status'] = 'simulated_failure'
                    self.db_manager.add_executed_trade(trade_data)
                    return None # La simulación falló, no hay nada que retornar
            # --- FIN DE LA LÓGICA DE PAPER TRADING ---

            # Si el modo paper trading está deshabilitado, el código continúa hacia la ejecución real.
            if not is_simulation_successful:
                self.logger.error(f"Swap en {dex_name} CANCELADO debido a fallo en la simulación.", extra=log_extra)
                trade_data['status'] = 'failed_simulation'
                self.db_manager.add_executed_trade(trade_data)
                if self.notification_manager:
                    await self.notification_manager.send_trade_failure_notification(trade_data)
                self.nonce_manager.reset_nonce()
                return None

            max_send_retries = self.settings_manager.get_setting("MAX_TRANSACTION_SEND_RETRIES", 3) if self.settings_manager else 3
            tx_timeout = self.settings_manager.get_setting("TX_TIMEOUT_SECONDS", 300) if self.settings_manager else 300

            tx_hash = None
            for attempt in range(max_send_retries):
                try:
                    signed_tx = self.w3.eth.account.sign_transaction(unsigned_tx, private_key=self.account.key)
                    tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
                    self.logger.info(f"Transacción de swap real enviada en {dex_name} (Intento {attempt + 1}/{max_send_retries}): {tx_hash.hex()}", extra=log_extra)
                    trade_data['tx_hash'] = tx_hash.hex()

                    receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=tx_timeout)

                    if receipt['status'] == 1:
                        self.logger.info(f"Transacción de swap exitosa en {dex_name}. Hash: {tx_hash.hex()}", extra=log_extra)
                        trade_data['status'] = 'success'

                        actual_gas_used = receipt['gasUsed']
                        actual_gas_price = gas_price_strategy
                        actual_gas_cost_wei = actual_gas_used * actual_gas_price

                        gas_cost_usdt = None
                        net_profit_usdt_final = None

                        if self.data_fetcher:
                            try:
                                gas_cost_bnb = self.w3.from_wei(actual_gas_cost_wei, 'ether')
                                # Reutilizamos el snapshot si es posible
                                bnb_price_usdt = price_snapshot.get('BNB_price_usdt')
                                if not bnb_price_usdt:
                                    current_prices = await self.data_fetcher.get_current_prices(quote_token_symbol="USDT")
                                    bnb_price_usdt = current_prices.get('WBNB')

                                if bnb_price_usdt is not None and bnb_price_usdt > 0:
                                    gas_cost_usdt = float(gas_cost_bnb) * bnb_price_usdt
                                    self.logger.debug(f"Costo de gas real calculado: {gas_cost_usdt:.6f} USDT", extra=log_extra)
                                else:
                                    self.logger.warning("No se pudo obtener el precio de WBNB para calcular costo de gas en USDT. Usando None.", extra=log_extra)
                            except Exception as e:
                                self.logger.error(f"Error al convertir gas cost a USDT: {e}", exc_info=True, extra=log_extra)
                        else:
                            self.logger.warning("DataFetcher no disponible, no se pudo calcular costo de gas en USDT.", extra=log_extra)

                        trade_data['gas_cost_usdt'] = gas_cost_usdt
                        if gross_profit_usdt is not None and gas_cost_usdt is not None:
                            net_profit_usdt_final = gross_profit_usdt - gas_cost_usdt
                        else:
                            net_profit_usdt_final = None
                        trade_data['net_profit_usdt'] = net_profit_usdt_final

                        self.db_manager.add_executed_trade(trade_data)
                        if self.notification_manager:
                            await self.notification_manager.send_trade_success_notification(trade_data)
                        return tx_hash.hex()
                    else:
                        self.logger.error(f"Transacción de swap fallida en {dex_name} (status=0). Hash: {tx_hash.hex()}", extra=log_extra)
                        trade_data['status'] = 'failed_reverted'
                        self.db_manager.add_executed_trade(trade_data)
                        if self.notification_manager:
                            await self.notification_manager.send_trade_failure_notification(trade_data)
                        self.nonce_manager.reset_nonce()
                        return None
                except (TransactionNotFound, TimeExhausted) as e:
                    self.logger.warning(f"La transacción {tx_hash.hex() if tx_hash else 'no encontrada'} se perdió o el tiempo de espera se agotó (Intento {attempt + 1}/{max_send_retries}). Error: {e}. Reintentando...", extra=log_extra, exc_info=True)
                    await asyncio.sleep(5 * (attempt + 1))
                    continue
                except Exception as e:
                    self.logger.error(f"Error inesperado al enviar/esperar transacción en {dex_name} (Intento {attempt + 1}/{max_send_retries}): {e}", extra=log_extra, exc_info=True)
                    break

            self.logger.error(f"Transacción de swap fallida en {dex_name} después de {max_send_retries} intentos de envío/confirmación.", extra=log_extra)
            trade_data['status'] = 'failed_unconfirmed'
            self.db_manager.add_executed_trade(trade_data)
            if self.notification_manager:
                await self.notification_manager.send_trade_failure_notification(trade_data)
            self.nonce_manager.reset_nonce()
            return None

        except ContractLogicError as e:
            self.logger.error(f"Error lógico del contrato para {dex_name} (revert): {e}", extra=log_extra, exc_info=True)
            trade_data['status'] = 'failed_contract_logic'
            self.db_manager.add_executed_trade(trade_data)
            if self.notification_manager:
                await self.notification_manager.send_trade_failure_notification(trade_data)
            self.nonce_manager.reset_nonce()
            return None
        except (ConnectionError, requests.exceptions.ConnectionError) as e:
            self.logger.warning(f"Error de conexión o red (reintentando) al ejecutar swap en {dex_name}: {e}", extra=log_extra, exc_info=True)
            trade_data['status'] = 'failed_network_retryable'
            self.db_manager.add_executed_trade(trade_data)
            self.nonce_manager.reset_nonce()
            raise
        except ValidationError as e:
            self.logger.error(f"Error de validación en los datos de la transacción para {dex_name}: {e}", extra=log_extra, exc_info=True)
            trade_data['status'] = 'failed_validation'
            self.db_manager.add_executed_trade(trade_data)
            if self.notification_manager:
                await self.notification_manager.send_trade_failure_notification(trade_data)
            self.nonce_manager.reset_nonce()
            return None
        except Exception as e:
            self.logger.critical(f"Error inesperado y no manejado al ejecutar swap en {dex_name}: {e}", extra=log_extra, exc_info=True)
            trade_data['status'] = 'failed_unexpected'
            self.db_manager.add_executed_trade(trade_data)
            if self.notification_manager:
                await self.notification_manager.send_trade_failure_notification(trade_data)
            self.nonce_manager.reset_nonce()
            return None

    # --- INICIO DEL MÉTODO INTEGRADO ---
    async def execute_multi_dex_route(self, route_detailed: List[Dict[str, Any]], initial_amount_in: int, gross_profit_usdt: float, correlation_id: Optional[str] = None) -> bool:
        """
        Ejecuta una ruta de arbitraje Multi-DEX de forma secuencial y segura.
        Retorna True si toda la ruta se completó exitosamente, False en caso contrario.
        """
        log_extra = {'correlation_id': correlation_id}
        self.logger.info(f"--- Iniciando Ejecución de Ruta Multi-DEX ---", extra=log_extra)
        
        if not self.data_fetcher:
            self.logger.critical("DataFetcher no está disponible. No se puede ejecutar la ruta Multi-DEX.", extra=log_extra)
            return False
            
        current_amount_in = initial_amount_in
        
        # Guardamos un registro maestro para la ruta completa
        full_route_trade_data = {
            'timestamp': int(time.time()),
            'route': " → ".join([step['token'] for step in route_detailed]),
            'tx_hash': f"multi_dex_{correlation_id}", # Un hash compuesto para la ruta completa
            'status': 'pending',
            'correlation_id': correlation_id,
            'gross_profit_usdt': gross_profit_usdt,
            'net_profit_usdt': None,
            'gas_cost_usdt': 0.0 # Iremos sumando el gas
        }

        for i in range(len(route_detailed) - 1):
            step_from = route_detailed[i]
            step_to = route_detailed[i+1]
            
            token_in_sym = step_from['token']
            token_out_sym = step_to['token']
            dex_name = step_to['dex']

            token_in_addr = self.data_fetcher.tokens_by_symbol[token_in_sym]
            token_out_addr = self.data_fetcher.tokens_by_symbol[token_out_sym]
            router_address = self.data_fetcher.dex_routers[dex_name]
            router_abi = self.data_fetcher.get_abi("router")

            # ---VALIDACIÓN ---
            if not router_abi:
                self.logger.critical(f"No se pudo obtener el ABI del router para {dex_name}. Abortando ruta.", extra=log_extra)
                full_route_trade_data['status'] = f'failed_step_{i+1}_no_abi'
                self.db_manager.add_executed_trade(full_route_trade_data)
                return False

            self.logger.info(f"Paso {i+1}/{len(route_detailed)-1}: Swap en {dex_name} de {token_in_sym} a {token_out_sym}", extra=log_extra)

            # Para el primer paso, usamos el monto inicial. Para los siguientes, usamos el balance TOTAL que tenemos.
            if i > 0:
                current_amount_in = self.get_token_balance(token_in_addr)
            
            if current_amount_in == 0:
                self.logger.error(f"¡FALLO CRÍTICO! Balance para el swap del paso {i+1} es 0. Abortando ruta.", extra=log_extra)
                full_route_trade_data['status'] = f'failed_step_{i+1}_zero_balance'
                self.db_manager.add_executed_trade(full_route_trade_data)
                return False

            tx_hash = await self.execute_swap(
                router_address=router_address, router_abi=router_abi,
                amount_in=current_amount_in, amount_out_min=1,
                path=[token_in_addr, token_out_addr],
                to_address=self.account.address, deadline=int(time.time()) + 300,
                dex_name=dex_name, gross_profit_usdt=None,
                correlation_id=f"{correlation_id}_step_{i+1}"
            )

            if not tx_hash:
                self.logger.error(f"Fallo en el paso {i+1} del swap (la transacción no se confirmó). Abortando ruta Multi-DEX.", extra=log_extra)
                full_route_trade_data['status'] = f'failed_step_{i+1}_tx_failed'
                self.db_manager.add_executed_trade(full_route_trade_data)
                return False

            await asyncio.sleep(8)
            
            balance_of_next_token = self.get_token_balance(token_out_addr)
            if balance_of_next_token == 0:
                self.logger.error(f"¡FALLO CRÍTICO DE RUTA! El swap en el paso {i+1} se ejecutó (Hash: {tx_hash}) pero el balance del token de salida es 0. Abortando.", extra=log_extra)
                full_route_trade_data['status'] = f'failed_step_{i+1}_no_output'
                full_route_trade_data['tx_hash'] += f";failed_at_{tx_hash}"
                self.db_manager.add_executed_trade(full_route_trade_data)
                if self.notification_manager:
                    await self.notification_manager.send_message("🆘 **¡ALERTA CRÍTICA!** 🆘\nRuta Multi-DEX rota. Fondos atascados en: " + token_out_sym)
                return False
            
            self.logger.info(f"Paso {i+1} completado. Balance de {token_out_sym}: {balance_of_next_token}", extra=log_extra)

        self.logger.info(f"--- ¡ÉXITO! Ruta Multi-DEX completada. ---", extra=log_extra)
        full_route_trade_data['status'] = 'success'
        self.db_manager.add_executed_trade(full_route_trade_data)
        if self.notification_manager:
            await self.notification_manager.send_trade_success_notification(full_route_trade_data)
        return True