# testkpi_synthetic.py
import networkx as nx
import random

# === Compatibility and weights ===
COMPATIBILITY = {
    "Cultural":    {"Cultural": 0.50, "Leisure": 0.92, "Office": 0.75, "Residential": 1.00, "Green": 0.83},
    "Leisure":     {"Cultural": 0.92, "Leisure": 0.66, "Office": 0.83, "Residential": 0.92, "Green": 1.00},
    "Office":      {"Cultural": 0.75, "Leisure": 0.83, "Office": 0.41, "Residential": 0.83, "Green": 0.66},
    "Residential": {"Cultural": 1.00, "Leisure": 0.91, "Office": 0.83, "Residential": 0.50, "Green": 1.00},
    "Green":       {"Cultural": 0.83, "Leisure": 1.00, "Office": 0.66, "Residential": 1.00, "Green": 0.58},
}

NODE_WEIGHTS = {
    "Cultural": 1.2, "Leisure": 1.1, "Office": 1.0, "Residential": 0.9, "Green": 1.3
}

TYPES = list(NODE_WEIGHTS.keys())

def generate_graph(num_nodes, mix_quality, connectivity, distance_scale):
    """
    num_nodes: total nodes
    mix_quality: "good", "medium", "poor" -> how well mixed the uses are
    connectivity: probability of edge creation
    distance_scale: average street length multiplier
    """
    G = nx.Graph()
    
    # Assign types
    if mix_quality == "good":
        types = [random.choice(TYPES) for _ in range(num_nodes)]
    elif mix_quality == "medium":
        # Slight bias towards clustering same types
        types = []
        base_type = random.choice(TYPES)
        for i in range(num_nodes):
            if random.random() < 0.7:
                types.append(base_type)
            else:
                types.append(random.choice(TYPES))
    else:  # poor
        # Almost all same type
        base_type = random.choice(TYPES)
        types = [base_type for _ in range(num_nodes)]
    
    for i in range(num_nodes):
        G.add_node(i, type=types[i])
    
    # Create edges with distances
    for i in range(num_nodes):
        for j in range(i+1, num_nodes):
            if random.random() < connectivity:
                dist = random.uniform(50, 200) * distance_scale
                G.add_edge(i, j, distance=dist)
    
    return G

def compute_score(G):
    typed_nodes = {n: G.nodes[n]["type"] for n in G.nodes if G.nodes[n]["type"] in NODE_WEIGHTS}
    typed_list = list(typed_nodes.keys())
    if len(typed_list) < 2:
        return 0.0

    score_sum = 0.0
    pair_count = 0
    paths_found = 0

    for comp in nx.connected_components(G):
        comp_typed = [n for n in comp if n in typed_nodes]
        if len(comp_typed) < 2:
            continue
        for i in range(len(comp_typed)):
            for j in range(i+1, len(comp_typed)):
                u, v = comp_typed[i], comp_typed[j]
                try:
                    dist = nx.shortest_path_length(G, source=u, target=v, weight="distance")
                except nx.NetworkXNoPath:
                    continue
                if dist > 0:
                    cu, cv = typed_nodes[u], typed_nodes[v]
                    ku, kv = NODE_WEIGHTS[cu], NODE_WEIGHTS[cv]
                    fuv = COMPATIBILITY[cu][cv]
                    score_sum += (ku * kv * fuv) / float(dist)
                    pair_count += 1
                    paths_found += 1

    N = len(typed_list)
    return (score_sum / (max(1, N) * max(1, pair_count))) if pair_count > 0 else 0.0

# === Define scenarios ===
scenarios = {
    "Ideal":  {"num_nodes": 50, "mix_quality": "good",   "connectivity": 0.6,  "distance_scale": 0.5},
    "Bueno":  {"num_nodes": 50, "mix_quality": "good",   "connectivity": 0.4,  "distance_scale": 1.0},
    "Medio":  {"num_nodes": 50, "mix_quality": "medium", "connectivity": 0.3,  "distance_scale": 1.2},
    "Malo":   {"num_nodes": 50, "mix_quality": "poor",   "connectivity": 0.2,  "distance_scale": 1.5},
    "Pésimo": {"num_nodes": 50, "mix_quality": "poor",   "connectivity": 0.1,  "distance_scale": 2.0},
}

print(f"{'Escenario':<10} | {'Score x1000':>10}")
print("-" * 25)

for name, params in scenarios.items():
    G = generate_graph(**params)
    score = compute_score(G) * 1000  # scaled like tu salida
    print(f"{name:<10} | {score:10.3f}")