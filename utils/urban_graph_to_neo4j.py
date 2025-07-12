from neo4j import GraphDatabase
import networkx as nx
import os

# Neo4j connection parameters
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "1234567890"  # Replace with your Neo4j password

# Load GraphML file
graphml_path = os.path.join("..", "knowledge", "urban_graph.graphml")
print(f"Loading GraphML from: {graphml_path}")
G_urban = nx.read_graphml(graphml_path)

print(f"Graph loaded: {G_urban.number_of_nodes()} nodes and {G_urban.number_of_edges()} edges")

# Initialize Neo4j driver
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def insert_graph(tx, graph):
    # Insert nodes
    for node_id, attrs in graph.nodes(data=True):
        query = """
        MERGE (n:UrbanNode {id: $id})
        SET n += $properties
        """
        tx.run(query, id=node_id, properties=attrs)

    # Insert relationships
    for source, target, attrs in graph.edges(data=True):
        rel_type = attrs.get('type', 'CONNECTED').upper()
        query = f"""
        MATCH (a:UrbanNode {{id: $source}})
        MATCH (b:UrbanNode {{id: $target}})
        MERGE (a)-[r:{rel_type}]->(b)
        SET r += $properties
        """
        tx.run(query, source=source, target=target, properties=attrs)

# Write data to Neo4j
with driver.session() as session:
    session.write_transaction(insert_graph, G_urban)

print("Urban graph successfully imported into Neo4j.")

# Close the connection
driver.close()
