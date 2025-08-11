# MaCAD 2025 / Master Thesis - Name TBD
This Master Thesis project is a copilot to improve architects knowledge on a site to improve masterplanning, by leveraging OSMX, GraphML (2D & 3D), LLM, UI, ... 

# 1_Brief to Graph
*Brief to Graph* step is translating natural language, not detailed, into a pair of csv for nodes and edges, enabling a graph representation, by using Large Language Model (LLM) (model to be determined e.g. [qwen3:8b](https://ollama.com/library/qwen3) or [llama3.1:8b](https://ollama.com/library/llama3.1:8b)) and Natural Language Preprocessing (NLP) preprocessing.

**Input** - Masterplan brief:
- e.g., “A mixed-use neighborhood along the riverfront... high walkability... civic plaza... integration with existing transport lines...”

**Output** - CSV of nodes & edges:
- CSV of nodes:
    fields like:
    - id,
    program,
    scale or size,
    denisty,
    typology,
    social weight or value,
    mobility relevance,
    ...
- CSV of edges:
    - source_id,
    target_id,
    connection_type, 
    intensity,
    integration_with_city,
    ...

**In between steps**
- brief preprocessing with NLP
- brief to json with LLM
- json to csv with LLM
- graph visualisation with Rhino/Gh

# 2_Graph to 2D layout

# 3_Graph to 3D layout

## Authors

- César Diego Herbosa [@cdherbosa](https://github.com/cdherbosa)
- Aymeric Brouez [@Aymrc](https://github.com/Aymrc)

## Supervisor
- David Andrés León - IAAC MaCAD Director