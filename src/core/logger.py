import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from constants import PROJECT_ROOT
from jsonformatter import JsonFormatter

# ---Filtro de Contexto ---
# Este filtro se asegura de que 'correlation_id' siempre esté presente
# en el registro del log, evitando KeyErrors.
class ContextFilter(logging.Filter):
    def filter(self, record):
        # Si 'correlation_id' no está en el registro, lo añadimos con un valor por defecto.
        if not hasattr(record, 'correlation_id'):
            record.correlation_id = 'N/A' # o None, si prefieres
        return True

class ArbitriumLogger(logging.Logger):
    def __init__(self, name: str, log_file: str):
        """
        Logger configurable que hereda de logging.Logger, con salida dual
        (archivo y consola) y rotación de archivos.

        Args:
            name: Nombre del logger (generalmente el nombre del módulo o clase).
            log_file: Nombre del archivo de log donde se guardarán los mensajes.
        """
        super().__init__(name)
        
        self.setLevel(logging.DEBUG) 

        log_dir = PROJECT_ROOT / "logs"
        log_dir.mkdir(exist_ok=True)
        
        file_formatter = JsonFormatter(
            '''{
                "timestamp": "%(asctime)s",
                "name": "%(name)s",
                "level": "%(levelname)s",
                "message": "%(message)s",
                "module": "%(module)s",
                "funcName": "%(funcName)s",
                "correlation_id": "%(correlation_id)s"
            }''',
            ensure_ascii=False
        )

        console_formatter = logging.Formatter('%(levelname)s: %(message)s')

        self.file_handler = RotatingFileHandler(
            log_dir / log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3 
        )
        self.file_handler.setFormatter(file_formatter)
        self.file_handler.setLevel(logging.DEBUG)

        self.console_handler = logging.StreamHandler()
        self.console_handler.setFormatter(console_formatter)
        self.console_handler.setLevel(logging.INFO)

        # ---Añadir filtro y handlers ---
        # Nos aseguramos de no añadir handlers y filtros múltiples veces si el logger ya existe.
        if not self.handlers:
            # Añadimos el filtro al logger principal.
            self.addFilter(ContextFilter())
            self.addHandler(self.file_handler)
            self.addHandler(self.console_handler)
            
        self.verbose = False

    def set_verbose(self, enabled: bool):
        """
        Activa/desactiva la salida detallada (DEBUG) a la consola.

        Args:
            enabled: Si es True, la consola mostrará mensajes DEBUG. Si es False, volverá a INFO.
        """
        self.verbose = enabled
        if enabled:
            self.console_handler.setLevel(logging.DEBUG)
        else:
            self.console_handler.setLevel(logging.INFO)