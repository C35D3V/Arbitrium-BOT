from typing import List, Tuple, Dict
from itertools import permutations
from collections import defaultdict

class RouteGenerator:
    @staticmethod
    def generate_triangular_routes(tokens: List[str], base_token: str = "USDT") -> List[Tuple[str, str, str, str]]:
        """
        Genera todas las rutas triangulares posibles: base → A → B → base.
        """
        routes = []
        for crypto_a, crypto_b in permutations(tokens, 2):
            if crypto_a != base_token and crypto_b != base_token:
                route = (base_token, crypto_a, crypto_b, base_token)
                routes.append(route)
        return routes

    @staticmethod
    def filter_duplicate_routes(routes: List[Tuple]) -> List[Tuple]:
        """Elimina rutas duplicadas (ej: USDT→A→B→USDT y USDT→B→A→USDT)."""
        unique_routes = []
        seen = set()
        for route in routes:
            key = (route[0], tuple(sorted([route[1], route[2]])), route[3])
            if key not in seen:
                seen.add(key)
                unique_routes.append(route)
        return unique_routes

    @staticmethod
    def generate_triangular_routes_from_pairs(tokens: List[str], pairs: List[Tuple[str, str]]) -> List[List[str]]:
        """
        Genera rutas triangulares de forma más eficiente a partir de una lista de pares viables.
        """
        graph = defaultdict(set)
        for a, b in pairs:
            graph[a].add(b)
            graph[b].add(a)
        
        unique_routes = set()
        
        for start_node in graph:
            for second_node in graph[start_node]:
                for third_node in graph[second_node]:
                    if third_node == start_node:
                        continue
                    
                    if start_node in graph[third_node]:
                        route_tuple = tuple(sorted((start_node, second_node, third_node)))
                        unique_routes.add(route_tuple)
        
        final_routes = [[r[0], r[1], r[2], r[0]] for r in unique_routes]
        return final_routes

    @staticmethod
    def find_paths_of_length_n(graph: Dict[str, set], start_node: str, n: int) -> List[List[str]]:
        """
        Encuentra todos los ciclos simples de longitud 'n' que comienzan y terminan en 'start_node'.
        Un ciclo simple no repite nodos intermedios.
        
        Args:
            graph: El grafo de adyacencia (ej: {'USDT': {'BNB', 'CAKE'}, ...}).
            start_node: El token con el que las rutas deben comenzar y terminar.
            n: La longitud deseada del ciclo (número de swaps).

        Returns:
            Una lista de rutas, donde cada ruta es una lista de tokens.
            Ej. para n=4: [['USDT', 'A', 'B', 'C', 'USDT']]
        """
        paths = []
        stack = [(start_node, [start_node])]
        
        while stack:
            current_node, path = stack.pop()
            
            if len(path) > n:
                continue
            
            if len(path) == n and start_node in graph.get(current_node, set()):
                final_path = path + [start_node]
                paths.append(final_path)
                continue

            for neighbor in graph.get(current_node, set()):
                if neighbor not in path:
                    new_path = path + [neighbor]
                    stack.append((neighbor, new_path))
                    
        return paths