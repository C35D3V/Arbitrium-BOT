# 🤖 Arbitrium BOT 🤖

Un bot de arbitraje de criptomonedas de alto rendimiento para la Binance Smart Chain (BSC), construido con Python y `web3.py`.

Este proyecto es capaz de monitorear múltiples DEXs (como PancakeSwap, BiSwap) en tiempo real, identificar oportunidades de arbitraje (Cross-DEX, Stablecoin y Multi-Step) y ejecutar transacciones de forma simulada ("paper trading") o real.

## 🚀 Características Principales

* **Múltiples Estrategias:**
    * **Cross-DEX:** Busca diferencias de precio para un mismo par en diferentes DEXs.
    * **Stablecoin:** Monitorea pares de stablecoins (ej. USDT/BUSD) para arbitraje de baja volatilidad.
    * **Multi-Step (Triangular):** Busca oportunidades de arbitraje cíclico (ej. USDT → BNB → CAKE → USDT) dentro de un mismo DEX o entre varios.
* **Monitoreo en Tiempo Real:** Utiliza WebSockets (`wss://`) para escuchar eventos `Sync` de los pools de liquidez, reaccionando a cambios de precio al instante.
* **Gestión de Riesgo:**
    * **Inversión Óptima:** Calcula el tamaño de inversión ideal para maximizar la ganancia neta, teniendo en cuenta el impacto en el precio y el "slippage".
    * **Slippage Dinámico:** Ajusta la tolerancia al deslizamiento basándose en la liquidez del pool.
* **Ejecución Segura:**
    * **Modo "Paper Trading":** Simula transacciones en un *fork* de la blockchain antes de ejecutarlas, asegurando la rentabilidad sin arriesgar fondos.
    * **Gestión de Nonce:** Manejo robusto del "nonce" de la billetera para evitar transacciones fallidas.
* **Backtesting:** Incluye un script (`backtester.py`) para simular estrategias con datos históricos de la base de datos.
* **Notificaciones:** Se integra con Telegram para enviar alertas sobre transacciones exitosas o fallidas.

## 🛠️ Stack Tecnológico

* **Core:** Python 3.10+ (con `asyncio`)
* **Blockchain:** `web3.py`
* **Comunicaciones:** `websockets`, `aiohttp`
* **Base de Datos:** `sqlite3`
* **Interfaz:** `rich` (para una terminal limpia)

## 📦 Instalación

1.  Clona este repositorio:
    ```bash
    git clone [https://github.com/C35D3V/Arbitrium-BOT.git](https://github.com/C35D3V/Arbitrium-BOT.git)
    cd Arbitrium-BOT
    ```
2.  (Recomendado) Crea un entorno virtual:
    ```bash
    python -m venv venv
    source venv/bin/activate  # En Windows: venv\Scripts\activate
    ```
3.  Instala las dependencias:
    ```bash
    pip install -r requirements.txt
    ```

## ⚙️ Configuración

El bot requiere un archivo `.env` para funcionar.

1.  Crea un archivo llamado `.env` en la raíz del proyecto.
2.  Añade las siguientes variables (nunca compartas este archivo):

    ```ini
    # Clave privada de tu billetera (Wallet) - ¡MANTENER EN SECRETO!
    PRIVATE_KEY=0xTU_CLAVE_PRIVADA...
    
    # Endpoints de la BSC (puedes obtenerlos de Alchemy, Infura, etc.)
    RPC_URL=[https://bsc-dataseed.binance.org/](https://bsc-dataseed.binance.org/)
    WSS_URL=wss://bsc-ws-node.nariox.org:443
    
    # (Opcional) API de Alchemy para precios
    ALCHEMY_API_KEY=TU_LLAVE_DE_ALCHEMY
    
    # (Opcional) Notificaciones de Telegram
    TELEGRAM_BOT_TOKEN=EL_TOKEN_DE_TU_BOT
    TELEGRAM_CHAT_ID=EL_ID_DE_TU_CHAT
    ```
3.  Revisa la configuración de los DEXs y tokens en `config/bsc_config.json` y los parámetros de estrategia en `config/user_settings.json`. Por defecto, el bot se inicia en modo **Paper Trading** (`PAPER_TRADING_MODE: true`).

## ▶️ Uso

Para iniciar el bot en modo interactivo:

```bash
python main.py
```

Para iniciarlo directamente en modo de monitoreo (ideal para servidores):

```bash
python main.py --task monitor
```

## ⚠️ ¡Advertencia Importante!

**Este es un proyecto de portafolio y debe ser tratado como software experimental.** El trading de criptomonedas es extremadamente arriesgado. El uso de este bot es bajo tu propio riesgo. No soy responsable por ninguna pérdida financiera. **NUNCA** uses claves privadas de billeteras con fondos reales que no estés dispuesto a perder.
