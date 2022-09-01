"""
read config info from blend file
"""
import bpy

print(f"Found render engine: {bpy.context.scene.render.engine}")
