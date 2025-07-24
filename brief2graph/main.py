# brief to graph LLM
import json, re, time, csv
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
def brief_to_json(brief_text):
    prompt = f"""
You are an expert urban planner. Read the following urban design brief and convert it into a JSON graph.

Brief:
\"\"\"
{brief_text}
\"\"\"

Your output should be a JSON object with two keys:

1. "nodes": a list of elements in the design (e.g., plaza, school, housing). Each node must have:
   - id (short identifier)
   - label (name)
   - typology (e.g., residential, civic)
   - scale (small, medium, large)
   - footprint (in m²)
   - social_weight (0.0 to 1.0)

2. "edges": a list of relationships between nodes. Each edge must have:
   - source
   - target
   - type (e.g., pedestrian, visual, transit)

Nodes can connnected to multiple nodes if the relation is logic. A node can be a sub-node part of a bigger topic(e.g. new_appartments can take place in the heritage_buildings)

Output only valid JSON. No explanations, no markdown. Just the JSON.
IMPORTANT: Do not wrap the JSON in markdown code fences. Return raw JSON only.
"""

    try:
        response = client.chat.completions.create(
            model="local-model",  # Replace with your model name
            messages=[
                {"role": "system", "content": "You convert urban planning briefs into structured JSON graph data."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )

        raw_output = response.choices[0].message.content

        # Remove markdown code fences if present
        clean_output = re.sub(r"^```(?:json)?\s*|```$", "", raw_output.strip(), flags=re.IGNORECASE | re.MULTILINE)

        # Try to parse the cleaned JSON
        try:
            parsed = json.loads(clean_output)
            return json.dumps(parsed, indent=2)
        except json.JSONDecodeError:
            return "// Still invalid after cleanup:\n" + clean_output

    except Exception as e:
        return f"// Error contacting local LLM: {str(e)}"

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
    except json.JSONDecodeError:
        return None, None, "// JSON invalid, can't convert to CSV"

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
        writer = csv.DictWriter(f, fieldnames=["source", "target", "type"])
        writer.writeheader()
        writer.writerows(edges)

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
