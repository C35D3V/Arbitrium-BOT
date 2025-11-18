import sqlite3
import time
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from core.logger import ArbitriumLogger

# Crear el directorio de datos si no existe
DATA_DIR = Path(__file__).parent.parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "arbitrium.db"

logger = ArbitriumLogger('database_manager', 'arbitrium.log')

class DatabaseManager:
    """
    Gestiona todas las operaciones con la base de datos SQLite para
    la persistencia de datos del bot.
    """
    def __init__(self, db_path: Path = DB_PATH):
        try:
            self.conn = sqlite3.connect(db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row # Permite acceder a las columnas por nombre
            self.cursor = self.conn.cursor()
            self._create_tables()
            logger.info(f"Conexión a la base de datos establecida en: {db_path}")
        except sqlite3.Error as e:
            logger.critical(f"Error fatal al conectar o inicializar la base de datos: {e}", exc_info=True)
            raise

    def _create_tables(self):
        """
        Crea las tablas necesarias en la base de datos si no existen.
        """
        try:
            # Tabla para cachear los pares descubiertos y acelerar el inicio
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS discovered_pairs (
                    pair_address TEXT PRIMARY KEY,
                    token0_address TEXT NOT NULL,
                    token1_address TEXT NOT NULL,
                    dex_name TEXT NOT NULL,
                    last_seen INTEGER NOT NULL
                )
            """)
            
            # Tabla para guardar un historial de oportunidades rentables
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS historic_opportunities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    route TEXT NOT NULL,
                    dex_name TEXT NOT NULL,
                    profit_usdt REAL NOT NULL,
                    profit_percent REAL NOT NULL,
                    correlation_id TEXT UNIQUE -- NUEVA COLUMNA
                )
            """)

            # Tabla para registrar todas las transacciones ejecutadas
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS executed_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    tx_hash TEXT UNIQUE,
                    route TEXT NOT NULL,
                    status TEXT NOT NULL, -- 'pending', 'success', 'failed', 'failed_simulation', etc.
                    gross_profit_usdt REAL, -- Ganancia bruta antes de costos de gas
                    net_profit_usdt REAL,   -- Ganancia neta después de costos de gas
                    gas_cost_usdt REAL,     -- Costo de gas en USDT
                    correlation_id TEXT,    -- NUEVO: Guardar el ID de correlación
                    price_snapshot TEXT     -- NUEVO: Columna para el snapshot de precios en formato JSON
                )
            """)
            self.conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error al crear las tablas de la base de datos: {e}", exc_info=True)
            self.conn.rollback()

    def add_discovered_pairs(self, pairs_data: List[Dict[str, Any]]):
        """
        Añade o actualiza una lista de pares descubiertos en la base de datos.
        
        Args:
            pairs_data: Una lista de diccionarios, cada uno con 
                        {'pair_address', 'token0', 'token1', 'dex_name'}.
        """
        if not pairs_data:
            return
            
        timestamp = int(time.time())
        to_insert = [
            (
                d['pair_address'], d['token0'], d['token1'], 
                d['dex_name'], timestamp
            ) for d in pairs_data
        ]
        
        try:
            # "OR REPLACE" actualizará el registro si la PRIMARY KEY (pair_address) ya existe
            self.cursor.executemany("""
                INSERT OR REPLACE INTO discovered_pairs 
                (pair_address, token0_address, token1_address, dex_name, last_seen) 
                VALUES (?, ?, ?, ?, ?)
            """, to_insert)
            self.conn.commit()
            logger.info(f"Añadidos/actualizados {len(to_insert)} pares en la base de datos.")
        except sqlite3.Error as e:
            logger.error(f"Error al insertar pares descubiertos: {e}", exc_info=True)
            self.conn.rollback()

    def get_all_discovered_pairs(self) -> List[Dict[str, Any]]:
        """
        Recupera todos los pares de liquidez guardados en la base de datos.
        """
        try:
            self.cursor.execute("SELECT pair_address, token0_address, token1_address, dex_name FROM discovered_pairs")
            rows = self.cursor.fetchall()
            
            # Convertir las tuplas a diccionarios para fácil uso
            return [dict(row) for row in rows]
        except sqlite3.Error as e:
            logger.error(f"Error al obtener pares de la base de datos: {e}", exc_info=True)
            return []

    def add_historic_opportunity(self, opportunity: Dict[str, Any]):
        """
        Guarda una oportunidad de arbitraje encontrada en el historial.
        """
        try:
            self.cursor.execute("""
                INSERT INTO historic_opportunities (timestamp, route, dex_name, profit_usdt, profit_percent, correlation_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                opportunity['timestamp'],
                " → ".join(opportunity['route']),
                opportunity['dex'],
                opportunity['profit_usdt'],
                opportunity['profit_percent'],
                opportunity.get('correlation_id') # Nuevo campo
            ))
            self.conn.commit()
        except sqlite3.Error as e:
            # Si el error es por la restricción UNIQUE, es probable que ya procesamos esta oportunidad.
            if "UNIQUE constraint failed" in str(e):
                 logger.warning(f"Intento de insertar oportunidad duplicada con correlation_id: {opportunity.get('correlation_id')}. Omitiendo.")
            else:
                logger.error(f"Error al guardar oportunidad histórica: {e}", exc_info=True)
            self.conn.rollback()

    def get_all_historic_opportunities(self) -> List[Dict[str, Any]]:
        """
        Recupera todas las oportunidades de arbitraje guardadas en el historial.
        """
        try:
            self.cursor.execute("SELECT timestamp, route, dex_name, profit_usdt, profit_percent, correlation_id FROM historic_opportunities ORDER BY timestamp DESC")
            rows = self.cursor.fetchall()
            opportunities = [dict(row) for row in rows]
            for opp in opportunities:
                opp['route'] = opp['route'].split(' → ') # Convertir de nuevo a lista
            return opportunities
        except sqlite3.Error as e:
            logger.error(f"Error al obtener oportunidades históricas: {e}", exc_info=True)
            return []

    def add_executed_trade(self, trade_data: Dict[str, Any]) -> Optional[int]:
        """
        Registra un intento de transacción y retorna su ID en la base de datos.
        """
        try:
            self.cursor.execute("""
                INSERT INTO executed_trades (timestamp, tx_hash, route, status, gross_profit_usdt, net_profit_usdt, gas_cost_usdt, correlation_id, price_snapshot)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_data.get('timestamp', int(time.time())),
                trade_data.get('tx_hash'),
                trade_data.get('route'),
                trade_data.get('status', 'pending'),
                trade_data.get('gross_profit_usdt'),
                trade_data.get('net_profit_usdt'),
                trade_data.get('gas_cost_usdt'),
                trade_data.get('correlation_id'),
                trade_data.get('price_snapshot') 
            ))
            self.conn.commit()
            return self.cursor.lastrowid
        except sqlite3.Error as e:
            logger.error(f"Error al registrar trade ejecutado: {e}", exc_info=True)
            self.conn.rollback()
            return None

    def get_all_executed_trades(self) -> List[Dict[str, Any]]:
        """
        Recupera todas las transacciones ejecutadas de la base de datos.
        """
        try:
            self.cursor.execute("SELECT * FROM executed_trades ORDER BY timestamp DESC")
            rows = self.cursor.fetchall()
            trades = [dict(row) for row in rows]
            for trade in trades:
                if trade.get('route'):
                    trade['route'] = trade['route'].split(' → ') # Convertir de nuevo a lista
            return trades
        except sqlite3.Error as e:
            logger.error(f"Error al obtener transacciones ejecutadas: {e}", exc_info=True)
            return []

    def close(self):
        """Cierra la conexión a la base de datos."""
        if self.conn:
            self.conn.close()
            logger.info("Conexión a la base de datos cerrada.")