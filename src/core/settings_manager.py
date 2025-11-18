import json
from pathlib import Path
from typing import Dict, Any, Optional
from core.logger import ArbitriumLogger
from constants import PROJECT_ROOT

CONFIG_DIR = PROJECT_ROOT / "config"
USER_SETTINGS_PATH = CONFIG_DIR / "user_settings.json"

logger = ArbitriumLogger('settings_manager', 'arbitrium.log')

class SettingsManager:
    """
    Gestiona la carga, guardado y acceso a la configuración dinámica del bot.
    Prioriza la configuración del usuario sobre los valores por defecto.
    """
    def __init__(self):
        self._default_settings: Dict[str, Any] = self._load_default_settings()
        self._user_settings: Dict[str, Any] = {}
        self.active_settings: Dict[str, Any] = {}
        self.load_settings()
        logger.info("SettingsManager inicializado y configuración cargada.")

    def _load_default_settings(self) -> Dict[str, Any]:
        """
        Define y carga los valores de configuración predeterminados del bot.
        Estos son los valores base si el usuario no ha configurado nada.
        """
        return {
            "MIN_PROFIT_THRESHOLD_USDT": 0.50,
            "DEFAULT_INVESTMENT_USDT": 100.0,
            "ARB_CHECK_INTERVAL_SECONDS": 15,
            "PRICE_UPDATE_INTERVAL_SECONDS": 10,
            "TX_TIMEOUT_SECONDS": 300,
            "MAX_TRANSACTION_SEND_RETRIES": 3,
            "AVERAGE_GAS_PER_SWAP": 150000,
            "USE_ADVANCED_GAS_API": True,
            "GAS_PRICE_MULTIPLIER_STANDARD": 1.05,
            "GAS_PRICE_MULTIPLIER_FAST": 1.20,
            "MIN_CROSS_DEX_PROFIT_PERCENTAGE": 0.2,
            "MIN_STABLECOIN_PROFIT_PERCENTAGE": 0.05,
            "MAX_INVESTMENT_OPTIMIZATION_USDT": 5000.0, # Límite superior para la búsqueda
            "OPTIMIZATION_ITERATIONS": 50, # Mayor número = más precisión
            
            "DEFAULT_SLIPPAGE_TOLERANCE": 0.005, # Lo mantenemos como un fallback
            
            # --- PARÁMETROS PARA SLIPPAGE DINÁMICO ---
            "USE_DYNAMIC_SLIPPAGE": True,
            "BASE_SLIPPAGE_PERCENT": 0.1, # El slippage mínimo que siempre aplicaremos (0.1%)
            "LIQUIDITY_IMPACT_FACTOR": 5.0, # Multiplicador para el impacto en la liquidez
            "MAX_SLIPPAGE_PERCENT": 2.5, # Un tope de seguridad para no usar slippage demasiado alto (2.5%)
            
            # ---PARÁMETRO PARA PAPER TRADING ---
            "PAPER_TRADING_MODE": True, # Por defecto, lo dejaremos activo para mayor seguridad

            # --- Configuración para notificaciones ---
            "TELEGRAM_BOT_TOKEN": None, # Ejemplo: "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
            "TELEGRAM_CHAT_ID": None    # Ejemplo: "123456789"
        }

    def load_settings(self):
        """
        Carga la configuración del usuario desde un archivo JSON.
        Si el archivo no existe o es inválido, usa solo los valores predeterminados.
        """
        self.active_settings = self._default_settings.copy()
        if USER_SETTINGS_PATH.exists():
            try:
                with open(USER_SETTINGS_PATH, 'r') as f:
                    self._user_settings = json.load(f)
                
                for key, value in self._user_settings.items():
                    # Solo cargar claves conocidas y con tipo de dato correcto
                    if key in self._default_settings and (self._default_settings[key] is None or isinstance(value, type(self._default_settings[key]))):
                        self.active_settings[key] = value
                    elif key in self._default_settings and not isinstance(value, type(self._default_settings[key])):
                        logger.warning(f"Tipo de dato incorrecto para '{key}' en user_settings.json. Usando valor por defecto.")
                    else:
                        logger.warning(f"Configuración desconocida '{key}' en user_settings.json. Ignorando.")
                logger.info("Configuración del usuario cargada y fusionada exitosamente.")
            except json.JSONDecodeError as e:
                logger.error(f"Error al leer user_settings.json (JSON inválido): {e}. Usando solo configuración por defecto.", exc_info=True)
                self._user_settings = {} # Reset user settings
            except Exception as e:
                logger.error(f"Error inesperado al cargar user_settings.json: {e}. Usando solo configuración por defecto.", exc_info=True)
                self._user_settings = {} # Reset user settings
        else:
            logger.info("user_settings.json no encontrado. Usando configuración por defecto.")
        
        # Guardar la configuración activa (crea el archivo si no existe, o actualiza con defaults si faltan)
        self.save_settings(self.active_settings)

    def save_settings(self, new_settings: Dict[str, Any]):
        """
        Guarda la configuración actual del bot en el archivo user_settings.json.
        """
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            # Solo guardar las claves que son parte de la configuración por defecto
            settings_to_save = {k: v for k, v in new_settings.items() if k in self._default_settings}
            with open(USER_SETTINGS_PATH, 'w') as f:
                json.dump(settings_to_save, f, indent=4)
            self.active_settings = new_settings.copy() # Actualizar la configuración activa
            logger.info(f"Configuración guardada en {USER_SETTINGS_PATH}.")
        except Exception as e:
            logger.error(f"Error al guardar la configuración en {USER_SETTINGS_PATH}: {e}", exc_info=True)

    def get_setting(self, key: str, default_value: Any = None) -> Any:
        """
        Obtiene el valor de una configuración. Prioriza la configuración activa.
        """
        # Si la clave no está en la configuración activa y no se proporciona un valor por defecto,
        # emitir una advertencia. Esto ayuda a detectar errores tipográficos o configuraciones faltantes.
        if key not in self.active_settings and default_value is None:
            logger.warning(f"Intento de acceder a configuración desconocida: '{key}'. Se recomienda definirla en _load_default_settings.")
            return None
        return self.active_settings.get(key, default_value)