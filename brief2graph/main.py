# brief to graph LLM
import json, re, time, csv, os
from openai import OpenAI

# graph viz
import networkx as nx
import matplotlib.pyplot as plt
import io
from PIL import Image

# UI
import gradio as gr
from gradio import Progress

# ==============================================

# Connect LM Studio
client = OpenAI(
    base_url="http://localhost:1234/v1",
    api_key="not-needed"
)

# Display LLM models
print("\n Available local LLM models in LM Studio:")
for model in client.models.list().data:
    print("–", model.id)
print("\n")




# LLM
# def brief_to_json(brief_text):
#     prompt = f"""
# You are an expert urban planner. Read the following urban design brief and convert it into a JSON graph.

# Brief:
# \"\"\"
# {brief_text}
# \"\"\"

# Your output should be a JSON object with two keys:

# 1. "nodes": a list of elements in the design (e.g., plaza, school, housing). Each node must have:
#    - id (short identifier)
#    - label (name)
#    - typology (e.g., residential, civic)
#    - scale (small, medium, large)
#    - footprint (in m²)
#    - social_weight (0.0 to 1.0)

# 2. "edges": a list of relationships between nodes. Each edge must have:
#    - source
#    - target
#    - type (e.g., pedestrian, visual, transit)

# Nodes can connnected to multiple nodes if the relation is logic. A node can be a sub-node part of a bigger topic(e.g. new_appartments can take place in the heritage_buildings)

# Output only valid JSON. No explanations, no markdown. Just the JSON.
# IMPORTANT: Do not wrap the JSON in markdown code fences. Return raw JSON only.
# """

#     try:
#         response = client.chat.completions.create(
#             model="local-model",  # Replace with your model name
#             messages=[
#                 {"role": "system", "content": "You convert urban planning briefs into structured JSON graph data."},
#                 {"role": "user", "content": prompt}
#             ],
#             temperature=0.2
#         )

#         raw_output = response.choices[0].message.content

#         # Remove markdown code fences if present
#         clean_output = re.sub(r"^```(?:json)?\s*|```$", "", raw_output.strip(), flags=re.IGNORECASE | re.MULTILINE)

#         # Try to parse the cleaned JSON
#         try:
#             parsed = json.loads(clean_output)
#             return json.dumps(parsed, indent=2)
#         except json.JSONDecodeError:
#             return "// Still invalid after cleanup:\n" + clean_output

#     except Exception as e:
#         return f"// Error contacting local LLM: {str(e)}"


def extract_first_json(text):
    """Extracts the first valid JSON object from any messy LLM output."""
    match = re.search(r'\{[\s\S]+\}', text)
    if match:
        return match.group(0)
    return None

# LLM
def brief_to_json(brief_text, save_to=None):
    prompt = f"""
You are an expert urban planner. Your task is to extract a buildable, programmatic graph from the following design brief:

Brief:
\"\"\"{brief_text}\"\"\"

Return a valid JSON object with two keys:

---

1. **"nodes"** — Each node represents a **physical program element** that can be assigned to a building or plot, such as:
- residential, office, retail, school, park, museum, library, etc.

Each node must include:
- `id`: short snake_case identifier
- `label`: readable title
- `typology`: one of ["residential", "commercial", "cultural", "public_space", "recreational", "office"]
- `footprint`: integer (area in m²)
- `scale`: one of ["small", "medium", "large"]
- `social_weight`: float between 0.0 and 1.0

Do NOT include abstract concepts (e.g., "mobility", "noise reduction"). Only assignable programs.

---

2. **"edges"** — Meaningful connections between nodes.

Each edge **must include**:
- `source`: node id
- `target`: node id
- `type`: one of ["contains", "mobility", "adjacent"]
- `mode`: a list of transport types (e.g., ["pedestrian", "bike", "bus", "train", "car"])

For "contains" relationships (e.g., connecting a program to the masterplan root), use:
```json
"type": "contains",
"mode": []

The resulting graph must follow realistic urban spatial logic and social needs. Follow these rules:

- Include one masterplan root node, and connect all other top-level program nodes to it via "contains" edges.
- "Contains" is only used for the masterplan or composite spaces (e.g., retail inside housing).
- Always link housing to work, leisure, and green spaces with "adjacent" or "mobility" edges.
- Mobility edges should reflect walkability and bike access first. Avoid car unless explicitly needed.
- Do not use "contains" unless the target node is spatially or functionally part of the source.
- Ensure edges reflect programmatic needs (e.g., libraries need access to residential areas).
- Always include pedestrian access.
- All edges must include a mode field. If the type is contains, the mode must be an empty list (mode: []). Never omit this field.
- All nodes must be unique program elements. No duplication or overlapping purposes.

"""

    try:
        response = client.chat.completions.create(
            model="local-model",
            messages=[
                {"role": "system", "content": "You are an expert urban planner who translates urban briefs into graph-based program logic with accurate relationships. Always follow spatial hierarchy, accessibility, and logical urban patterns."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )

        raw_output = response.choices[0].message.content
        # clean_output = re.sub(r"^```(?:json)?\s*|```$", "", raw_output.strip(), flags=re.IGNORECASE | re.MULTILINE)
        clean_output = extract_first_json(raw_output)

        if not clean_output:
            return "Could not find valid JSON object in LLM response:\n" + raw_output



        try:
            # print("\n--- RAW OUTPUT FROM LLM ---\n")
            # print(raw_output)
            # print("\n--- CLEANED JSON TEXT ---\n")
            # print(clean_output)

            parsed = json.loads(clean_output)

            # Optionally save as JSON or CSV
            if save_to:
                json_path = os.path.join(save_to, "brief_graph.json")
                with open(json_path, "w") as f:
                    json.dump(parsed, f, indent=2)

                # CSV export
                with open(os.path.join(save_to, "nodes.csv"), "w", newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=["id", "label", "typology", "scale", "footprint", "social_weight"]) # update with LLM
                    writer.writeheader()
                    writer.writerows(parsed["nodes"])

                with open(os.path.join(save_to, "edges.csv"), "w", newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=["source", "target", "type", "mode"]) # update with LLM
                    writer.writeheader()
                    writer.writerows(parsed["edges"])

            return json.dumps(parsed, indent=2)

        except json.JSONDecodeError:
            return "Still invalid after cleanup:\n" + clean_output

    except Exception as e:
        return f"Error contacting LLM: {str(e)}"

def clean_graph_schema(data):
    # Ensure required fields exist for nodes
    for node in data.get("nodes", []):
        node.setdefault("typology", "")
        node.setdefault("footprint", 0)
        node.setdefault("scale", "")
        node.setdefault("social_weight", 0.5)

    # Ensure required fields exist for edges
    for edge in data.get("edges", []):
        edge.setdefault("type", "mobility")  # default type if missing

        # Ensure mode is present and formatted as list
        if "mode" not in edge or edge["mode"] is None:
            edge["mode"] = []
        elif isinstance(edge["mode"], str):
            edge["mode"] = [edge["mode"]]
        elif not isinstance(edge["mode"], list):
            edge["mode"] = []

    return data

# Save JSON to a file
def save_json_file(brief_text):
    content = brief_to_json(brief_text)
    file_path = "brief_output.json"
    with open(file_path, "w") as f:
        f.write(content)
    return file_path

# JSON to CSV ~ 17sec
def convert_json_to_csv_files(brief_text):
    progress = gr.Progress(track_tqdm=False)
    progress(0.1, desc="Calling local LLM...")
    time.sleep(0.5)


    json_str = brief_to_json(brief_text)

    try:
        data = json.loads(json_str)
        data = clean_graph_schema(data)

    except json.JSONDecodeError as e:
        return None, None, f"JSON parsing failed: {str(e)}\n\n{json_str}"


    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    progress(0.4, desc="Saving nodes.csv")
    time.sleep(0.5)

    nodes_path = "brief2graph/nodes.csv"
    with open(nodes_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "label", "typology", "scale", "footprint", "social_weight"]) # update with LLM
        writer.writeheader()
        writer.writerows(nodes)

    progress(0.7, desc="Saving edges.csv")
    time.sleep(0.5)

    edges_path = "brief2graph/edges.csv"
    with open(edges_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "target", "type", "mode"]) # update with LLM
        writer.writeheader()
        for edge in edges:
            edge_copy = edge.copy()
            mode_value = edge.get("mode", [])
            if isinstance(mode_value, list):
                edge_copy["mode"] = ", ".join(mode_value)
            elif isinstance(mode_value, str):
                edge_copy["mode"] = mode_value
            else:
                edge_copy["mode"] = ""
            writer.writerow(edge_copy)



    progress(1.0, desc="Done!")
    time.sleep(0.5)
    return nodes_path, edges_path, json_str


# Graph view
def visualize_csv_graph(nodes_file, edges_file):
    G = nx.DiGraph()

    # Load nodes
    with open(nodes_file.name, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            G.add_node(row["id"], label=row["label"])

    # Load edges
    with open(edges_file.name, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            G.add_edge(row["source"], row["target"], label=row["type"])

    # Draw the graph
    plt.figure(figsize=(10, 8))
    pos = nx.spring_layout(G, k=0.5, seed=42)
    labels = nx.get_node_attributes(G, 'label')
    nx.draw(G, pos, with_labels=True, labels=labels, node_color="skyblue", node_size=1200, font_size=8, edge_color="gray")
    edge_labels = nx.get_edge_attributes(G, 'label')
    nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels, font_size=7)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight")
    buf.seek(0)
    plt.close()

    # Convert buffer to PIL image
    image = Image.open(buf)
    return image

# ==============================================

# Gradio UI
with gr.Blocks() as demo:
    gr.Markdown("## Urban Brief → Graph JSON")

    with gr.Row():
        brief_input = gr.Textbox(label="Paste your urban design brief", lines=12, placeholder="e.g. A mixed-use site with housing, school, and public plaza...")
        run_button = gr.Button("Run")

    json_output = gr.Code(label="Generated JSON", language="json", lines=15)


    with gr.Row():
        csv_nodes = gr.File(label="nodes.csv", visible=True)
        csv_edges = gr.File(label="edges.csv", visible=True)

    # Hook everything
    run_button.click(
        convert_json_to_csv_files,
        inputs=[brief_input],
        outputs=[csv_nodes, csv_edges, json_output],
        show_progress=True
    )


    # Graph view
    nodes_input = gr.File(label="nodes.csv", file_types=[".csv"])
    edges_input = gr.File(label="edges.csv", file_types=[".csv"])
    graph_image = gr.Image(type="pil", label="Graph Visualization")

    graph_btn = gr.Button("Generate Graph")

    graph_btn.click(
        visualize_csv_graph,
        inputs=[nodes_input, edges_input],
        outputs=graph_image
    )


demo.launch()
