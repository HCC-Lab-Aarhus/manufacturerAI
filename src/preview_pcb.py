#!/usr/bin/env python3
import bpy
import sys
import os
import json

def load_config(config_path):
    with open(config_path, 'r') as f:
        return json.load(f)

def create_preview():
    # Clear existing mesh objects
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    
    # Load the generated blend file
    bpy.ops.wm.open_mainfile(filepath="outputs/run1/generated_remote.blend")
    
    # Set up camera and lighting for better view
    bpy.ops.object.camera_add(location=(300, 300, 200))
    camera = bpy.context.object
    camera.rotation_euler = (1.2, 0, 0.8)
    
    # Add lighting
    bpy.ops.object.light_add(type='SUN', location=(200, 200, 300))
    sun = bpy.context.object
    sun.data.energy = 5
    
    # Position all objects for better view
    for obj in bpy.context.scene.objects:
        if obj.type == 'MESH':
            if 'back_cover' in obj.name.lower():
                obj.location = (0, 60, 0)  # Move back cover to show it separately
            elif 'battery' in obj.name.lower():
                obj.location = (60, 0, 0)
            else:
                obj.location = (0, 0, 0)
    
    # Set render settings
    bpy.context.scene.render.resolution_x = 1920
    bpy.context.scene.render.resolution_y = 1080
    bpy.context.scene.camera = camera
    
    # Render the preview
    bpy.context.scene.render.filepath = "outputs/run1/pcb_remote_preview.png"
    bpy.ops.render.render(write_still=True)
    
    print("PCB Remote preview rendered to: outputs/run1/pcb_remote_preview.png")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: blender -b --python preview_pcb.py")
        sys.exit(1)
    
    create_preview()