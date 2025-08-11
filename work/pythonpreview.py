import clr
clr.AddReference("Grasshopper")
import Grasshopper
import Grasshopper.Kernel
import Grasshopper.Instances

gh_path = r"C:\Users\broue\Documents\MyPreviewBox.gh"

def open_gh_file(path):
    canvas = Grasshopper.Instances.DocumentEditor
    app = Grasshopper.Instances
    io = app.GH_IO
    doc = app.ComponentServer.ReadDocument(path)
    if doc:
        app.DocumentServer.AddDocument(doc)
        print("Grasshopper file loaded.")
    else:
        print("Failed to load file.")

open_gh_file(gh_path)
