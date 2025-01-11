"""
read config info from blend file
write any settings overrides provided by user since upload
these are strictly settings that'd be set in the file itself and not provided in CLI command
"""
import bpy
import json
import sys
# get all args after "--", which allows us to ignore blender command args and only use args for this script
argv = sys.argv
argv = argv[argv.index("--") + 1:]

print(f"Found render engine: {bpy.context.scene.render.engine}")

task_dir = argv[0]
with open(f"{task_dir}/render_settings.json", "r") as f:
    settings = json.load(f)

frame_step = settings.get("frame_step")
if frame_step is not None:
    bpy.context.scene.frame_step = int(frame_step)
width = settings.get("resolution_x")
if width is not None:
    bpy.context.scene.render.resolution_x = int(width)
height = settings.get("resolution_y")
if height is not None:
    bpy.context.scene.render.resolution_y = int(height)
resolution_percentage = settings.get("resolution_percentage")
if resolution_percentage is not None:
    bpy.context.scene.render.resolution_percentage = int(resolution_percentage)
output_file_format = settings.get("output_file_format")
if output_file_format is not None:
    bpy.context.scene.render.image_settings.file_format = output_file_format
engine = settings.get("engine")
if engine is not None:
    bpy.context.scene.render.engine = engine
else:
    engine = bpy.context.scene.render.engine
samples = settings.get("pixel_samples")
if samples is not None:
    samples = int(samples)
    # NOTE: partially duplicated in scanner blender_scanner.py
    if engine == "CYCLES":
        bpy.context.scene.cycles.samples = samples
    elif engine == "BLENDER_EEVEE":
        bpy.context.scene.eevee.taa_render_samples = samples
    else:
        bpy.context.scene.display.render_aa = samples
selected_camera = settings.get("selected_camera")
if selected_camera:
    selected_camera_obj = bpy.context.scene.camera
    for obj in bpy.data.objects:
        if obj.type == "CAMERA" and obj.name == selected_camera:
            selected_camera_obj = obj
            break
    
    bpy.context.scene.camera = selected_camera_obj

# TODO can't find output file name setting in blender (output path exists, but this shouldn't matter to customer), skipping for now
# maybe it's just to name the downloaded zip file?
# output_filename = settings.get("output_filename")
# if output_filename:
#     bpy.context.scene. = output_filename
use_motion_blur = settings.get("use_motion_blur")
if use_motion_blur is not None:
    use_motion_blur = use_motion_blur == "true"
    bpy.context.scene.render.use_motion_blur = use_motion_blur
# TODO omitting because I don't understand how this would work via a web UI, I think they need to draw a rectangle
# maybe it's mainly so if they upload with rectangle they can later disable it?
# border_rendering = settings.get("border_rendering")
# if border_rendering:
#     bpy.context.scene.render.use_border = border_rendering
use_compositing = settings.get("use_compositing")
if use_compositing is not None:
    use_compositing = use_compositing == "true"
    bpy.context.scene.render.use_compositing = use_compositing
use_sequencer = settings.get("use_sequencer")
if use_sequencer is not None:
    use_sequencer = use_sequencer == "true"
    bpy.context.scene.render.use_sequencer = use_sequencer
use_stamp_note = settings.get("use_stamp_note")
stamp_note = settings.get("stamp_note")
if use_stamp_note is not None:
    use_stamp_note = use_stamp_note == "true"
    bpy.context.scene.render.use_stamp_note = use_stamp_note
    if use_stamp_note:
        bpy.context.scene.render.stamp_note_text = stamp_note
# only used in cycles
if engine == "CYCLES":
    use_noise_threshold = settings.get("use_noise_threshold")
    noise_threshold = settings.get("noise_threshold")
    if use_noise_threshold is not None:
        use_noise_threshold = use_noise_threshold == "true"
        bpy.context.scene.cycles.use_adaptive_sampling = use_noise_threshold
        if use_noise_threshold:
            bpy.context.scene.cycles.adaptive_threshold = float(noise_threshold)

# ensure changes are persistent
bpy.ops.wm.save_mainfile()
