import Rhino
import scriptcontext as sc
import rhinoscriptsyntax as rs
import time
import threading

from config import layer_name # project variables

TARGET_LAYER_NAME = layer_name
listener_active = True
is_running = False
debounce_timer = None

def is_on_target_layer(rh_obj):
    layer_index = rh_obj.Attributes.LayerIndex
    layer = sc.doc.Layers[layer_index]
    return layer.Name == TARGET_LAYER_NAME

def handle_layer_change():
    Rhino.RhinoApp.WriteLine("[rhino_listener] Geometry on '{}' layer changed.".format(TARGET_LAYER_NAME))
    # Insert your actual logic here

def debounce_trigger():
    global debounce_timer
    if debounce_timer and debounce_timer.is_alive():
        return

    def delayed():
        time.sleep(1.0)
        if not listener_active or is_running:
            return
        handle_layer_change()

    debounce_timer = threading.Thread(target=delayed)
    debounce_timer.start()

def on_add(sender, e):
    if listener_active and is_on_target_layer(e.Object):
        debounce_trigger()

def on_modify(sender, e):
    if listener_active and is_on_target_layer(e.Object):
        debounce_trigger()

def on_replace(sender, e):
    if listener_active and is_on_target_layer(e.NewObject):
        debounce_trigger()

def on_delete(sender, e):
    # Cannot reliably check layer on delete
    debounce_trigger()

def setup_layer_listener():
    Rhino.RhinoDoc.AddRhinoObject += on_add
    Rhino.RhinoDoc.ModifyObjectAttributes += on_modify
    Rhino.RhinoDoc.ReplaceRhinoObject += on_replace
    Rhino.RhinoDoc.DeleteRhinoObject += on_delete
    Rhino.RhinoApp.WriteLine("[rhino_listener] Layer-specific listener active on '{}'.".format(TARGET_LAYER_NAME))

def remove_layer_listener():
    try: Rhino.RhinoDoc.AddRhinoObject -= on_add
    except: pass
    try: Rhino.RhinoDoc.ModifyObjectAttributes -= on_modify
    except: pass
    try: Rhino.RhinoDoc.ReplaceRhinoObject -= on_replace
    except: pass
    try: Rhino.RhinoDoc.DeleteRhinoObject -= on_delete
    except: pass
    Rhino.RhinoApp.WriteLine("[rhino_listener] Layer listener removed.")

def shutdown_listener():
    global listener_active
    listener_active = False
    remove_layer_listener()
    Rhino.RhinoApp.WriteLine("[rhino_listener] Listener shut down.")
