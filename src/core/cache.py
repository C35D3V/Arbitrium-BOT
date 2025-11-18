import time
from typing import Dict, Any, Optional, Callable, TypeVar, Protocol
from functools import wraps
from .logger import ArbitriumLogger
import asyncio

logger = ArbitriumLogger('cache_manager', 'arbitrium.log')

R = TypeVar('R', covariant=True)

class Cacheable(Protocol[R]):
    """
    Protocolo que describe una función decorada con cache_data.
    Indica que el objeto es llamable y tiene un método clear_cache.
    """
    clear_cache: Callable[[Optional[str]], None]
    
    def __call__(self, *args: Any, **kwargs: Any) -> R:
        ...

class DataCache:
    def __init__(self, ttl: int = 300, maxsize: Optional[int] = None):
        """
        Inicializa el caché con un tiempo de vida (TTL) en segundos.
        Opcionalmente, puede tener un tamaño máximo (LRU no implementado para esta versión).

        Args:
            ttl: Tiempo de vida de los datos en caché (default: 5 minutos).
            maxsize: Tamaño máximo del caché (actualmente no implementado para LRU, solo TTL).
        """
        self._cache: Dict[str, Any] = {}
        self._ttl = ttl
        self._last_updated: Dict[str, float] = {}
        self._maxsize = maxsize
        
        if self._maxsize is not None:
            logger.warning("DataCache: 'maxsize' parameter is currently not implemented.")

    def get(self, key: str) -> Optional[Any]:
        """
        Obtiene datos del caché si existen y no han expirado.

        Args:
            key: Identificador único para el conjunto de datos

        Returns:
            Los datos en caché o None si no existen o han expirado
        """
        if key not in self._cache:
            return None
            
        if self._maxsize and len(self._cache) > self._maxsize:
            # Lógica básica de limpieza si se supera el tamaño máximo
            oldest_key = next(iter(self._last_updated))
            del self._cache[oldest_key]
            del self._last_updated[oldest_key]

        last_updated = self._last_updated.get(key, 0)
        if time.time() - last_updated > self._ttl:
            logger.debug(f"Datos en caché para '{key}' han expirado. Eliminando.")
            del self._cache[key]
            del self._last_updated[key]
            return None
            
        logger.debug(f"Retornando datos desde caché para '{key}'.")
        return self._cache[key]

    def set(self, key: str, data: Any):
        """
        Almacena datos en el caché y registra el tiempo de actualización.
        """
        self._cache[key] = data
        self._last_updated[key] = time.time()
        logger.debug(f"Datos almacenados en caché para '{key}'.")

    def clear(self, key: Optional[str] = None):
        """
        Limpia una clave específica o todo el caché.
        """
        if key:
            if key in self._cache:
                del self._cache[key]
                del self._last_updated[key]
                logger.debug(f"Caché para '{key}' limpiado.")
        else:
            self._cache.clear()
            self._last_updated.clear()
            logger.debug("Todo el caché ha sido limpiado.")

def cache_data(ttl: int = 60, maxsize: Optional[int] = None) -> Callable[[Callable[..., R]], Cacheable[R]]:
    def decorator(func: Callable[..., R]) -> Cacheable[R]:
        _cache_instance = DataCache(ttl=ttl, maxsize=maxsize)
        
        @wraps(func)
        async def wrapper(*args, **kwargs) -> R:
            # Excluir 'self' del argumento si es un método de instancia
            cache_args = args[1:] if args and hasattr(args[0], func.__name__) else args
            cache_key = f"{func.__name__}_{str(cache_args)}_{str(kwargs)}"

            cached_result = _cache_instance.get(cache_key)
            if cached_result is not None:
                return cached_result
            
            try:
                # Comprobamos si la función original que estamos decorando es asíncrona.
                if asyncio.iscoroutinefunction(func):
                    fresh_result = await func(*args, **kwargs)
                else:
                    # Si no lo es, la llamamos como una función normal.
                    fresh_result = func(*args, **kwargs)

                _cache_instance.set(cache_key, fresh_result)
                return fresh_result
            except Exception as e:
                logger.error(f"Error en función cacheada '{cache_key}': {str(e)}", exc_info=True)
                expired_result = _cache_instance._cache.get(cache_key)
                if expired_result is not None:
                    logger.warning(f"Retornando datos expirados para '{cache_key}'")
                    return expired_result
                raise

        setattr(wrapper, 'clear_cache', _cache_instance.clear)
        
        return wrapper # type: ignore[return-value]
    return decorator