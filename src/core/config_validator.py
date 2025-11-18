from jsonschema import validate, ValidationError
import json
from typing import Dict, Any
from pathlib import Path
from .logger import ArbitriumLogger
from web3 import Web3

logger = ArbitriumLogger('config_validator', 'arbitrium.log')

# Esquema para configuración BSC actualizado
BSC_CONFIG_SCHEMA = {
    "type": "object",
    "required": ["dexes", "tokens", "abis"],
    "properties": {
        "dexes": {
            "type": "object",
            "minProperties": 1,
            "patternProperties": {
                "^[a-zA-Z0-9_]+$": {
                    "type": "object",
                    "required": ["router", "factory", "fee"],
                    "properties": {
                        "router": {
                            "type": "string",
                            "pattern": "^0x[a-fA-F0-9]{40}$"
                        },
                        "factory": {
                            "type": "string",
                            "pattern": "^0x[a-fA-F0-9]{40}$"
                        },
                        "fee": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1
                        }
                    }
                }
            }
        },
        "tokens": {
            "type": "object",
            "minProperties": 2,
            "patternProperties": {
                "^[A-Za-z0-9]+$": {
                    "type": "string",
                    "pattern": "^0x[a-fA-F0-9]{40}$"
                }
            },
            "additionalProperties": False
        },
        "abis": {
            "type": "object",
            "required": ["router", "factory", "pair"],
            "properties": {
                "router": {
                    "type": "array",
                    "items": {"$ref": "#/definitions/abiItem"}
                },
                "factory": {
                    "type": "array",
                    "items": {"$ref": "#/definitions/abiItem"}
                },
                "pair": {
                    "type": "array",
                    "items": {"$ref": "#/definitions/abiItem"}
                }
            }
        }
    },
    "definitions": {
        "abiItem": {
            "type": "object",
            "required": ["type"],
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["function", "event", "constructor", "receive", "fallback"]
                },
                "name": {"type": "string"},
                "inputs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "type"],
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string"},
                            "internalType": {"type": "string"}
                        }
                    }
                },
                "outputs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "type"],
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string"},
                            "internalType": {"type": "string"}
                        }
                    }
                },
                "stateMutability": {
                    "type": "string",
                    "enum": ["pure", "view", "nonpayable", "payable"]
                }
            }
        }
    }
}

def validate_bsc_config(config_path: Path) -> Dict[str, Any]:
    """Valida y carga el archivo de configuración BSC con manejo robusto de ABIs"""
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        # Conversión robusta de ABIs
        for abi_type in ['router', 'factory', 'pair']:
            # Puede que un abi no esté presente, lo manejamos con get
            abi_value = config.get('abis', {}).get(abi_type)
            
            if isinstance(abi_value, str):
                try:
                    cleaned_abi = abi_value.strip()
                    if cleaned_abi.startswith("'") and cleaned_abi.endswith("'"):
                        cleaned_abi = cleaned_abi[1:-1]
                    config['abis'][abi_type] = json.loads(cleaned_abi)
                except json.JSONDecodeError as e:
                    raise ValueError(f"ABI {abi_type} no es un JSON válido: {str(e)}\nContenido: {abi_value[:100]}")
            elif not isinstance(abi_value, list):
                if abi_value is None:
                    config['abis'][abi_type] = []
                else:
                    raise ValueError(f"ABI {abi_type} debe ser una lista o string JSON válido. Tipo recibido: {type(abi_value)}")
            
            if 'abis' in config and abi_type in config['abis'] and not all(isinstance(item, dict) for item in config['abis'][abi_type]):
                raise ValueError(f"ABI {abi_type} debe contener solo objetos JSON")
        
        # Validación completa del esquema
        validate(instance=config, schema=BSC_CONFIG_SCHEMA)
        
        # Verificación adicional de direcciones
        for dex_name, dex_config in config['dexes'].items():
            Web3.to_checksum_address(dex_config['router'])
            Web3.to_checksum_address(dex_config['factory'])
        
        for token_addr in config['tokens'].values():
            Web3.to_checksum_address(token_addr)
        
        logger.info("Configuración BSC validada correctamente")
        return config
        
    except json.JSONDecodeError as e:
        logger.critical(f"Error en formato JSON del archivo: {str(e)}")
        raise
    except ValueError as e:
        logger.critical(f"Error en valores de configuración: {str(e)}")
        raise
    except ValidationError as e:
        logger.critical(f"Error en esquema de configuración: {e.message}")
        logger.debug(f"Error path: {e.json_path}")
        raise
    except Exception as e:
        logger.critical(f"Error inesperado validando configuración: {str(e)}")
        raise