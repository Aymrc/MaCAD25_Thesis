import rhinoscriptsyntax as rs
import json
import os

def create_layers_from_json(json_path):
    if not os.path.exists(json_path):
        rs.MessageBox("JSON file not found:\n{}".format(json_path))
        return

    try:
        with open(json_path, 'r') as f:
            data = json.load(f)
    except Exception as e:
        rs.MessageBox("Failed to parse JSON:\n{}".format(str(e)))
        return

    layers = data.get("layers", [])
    for layer in layers:
        name = layer.get("name")
        color = layer.get("color", [0, 0, 0])  # Default to black if not set

        if not name:
            continue

        if not rs.IsLayer(name):
            rs.AddLayer(name, color=color)

    rs.MessageBox("Layers created successfully.")

# Example usage
json_file = os.path.join(os.path.dirname(__file__), "layers.json")
create_layers_from_json(json_file)
