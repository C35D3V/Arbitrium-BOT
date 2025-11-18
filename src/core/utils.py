from typing import List

def calculate_profit(amount_in: float, amount_out: float) -> float:
    """Calcula el porcentaje de ganancia."""
    # Asegurarse de que amount_in no sea cero para evitar ZeroDivisionError
    if amount_in == 0:
        return 0.0 # O levantar un ValueError, dependiendo del comportamiento deseado
    return ((amount_out - amount_in) / amount_in) * 100

def format_route(route: List[str]) -> str:
    """Convierte una ruta en string (ej: "USDT → BNB → CAKE")."""
    return " → ".join(route)
