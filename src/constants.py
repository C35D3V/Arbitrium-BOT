from pathlib import Path

# ----------------------------
# Rutas de configuración
# ----------------------------
PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
BSC_CONFIG_PATH = CONFIG_DIR / "bsc_config.json"
SOLANA_CONFIG_PATH = CONFIG_DIR / "solana_config.json"

# ----------------------------
# Otras constantes
# ----------------------------
DEFAULT_GAS_LIMIT = 300000
DEFAULT_SLIPPAGE = 0.005  # 0.5%
MAX_RETRIES = 3

DEFAULT_TTL_CACHE = 30  # segundos
MAX_RPC_RETRIES = 3
RPC_RETRY_DELAY = 5  # segundos

MIN_LIQUIDITY = 1000  # USD
PRICE_UPDATE_INTERVAL = 30  # segundos
ARB_SCAN_INTERVAL = 60  # segundos

# ----------------------------
# Estrategia de Gas
# ----------------------------
GAS_PRICE_MULTIPLIER_STANDARD = 1.05  # 5% por encima del precio de red
GAS_PRICE_MULTIPLIER_FAST = 1.20      # 20% por encima para transacciones urgentes