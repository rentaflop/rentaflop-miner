"""
read config info from blend file
write any settings overrides provided by user since upload
these are strictly settings that'd be set in the file itself and not provided in CLI command
"""
import bpy
import json
import os
from config import CACHE_DIR
import sys


def modify_settings_with_overrides():
    """
    read settings file and override the values currently in the .blend
    """
    # get all args after "--", which allows us to ignore blender command args and only use args for this script
    argv = sys.argv
    argv = argv[argv.index("--") + 1:]

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
    print(f"Found render engine: {engine}")
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
        # bool values in settings will be actual python bools
        bpy.context.scene.render.use_motion_blur = use_motion_blur
    # TODO omitting because I don't understand how this would work via a web UI, I think they need to draw a rectangle
    # maybe it's mainly so if they upload with rectangle they can later disable it?
    # border_rendering = settings.get("border_rendering")
    # if border_rendering:
    #     bpy.context.scene.render.use_border = border_rendering
    use_compositing = settings.get("use_compositing")
    if use_compositing is not None:
        bpy.context.scene.render.use_compositing = use_compositing
    use_sequencer = settings.get("use_sequencer")
    if use_sequencer is not None:
        bpy.context.scene.render.use_sequencer = use_sequencer
    use_stamp_note = settings.get("use_stamp_note")
    stamp_note = settings.get("stamp_note")
    if use_stamp_note is not None:
        bpy.context.scene.render.use_stamp_note = use_stamp_note
        if use_stamp_note:
            bpy.context.scene.render.stamp_note_text = stamp_note
    # only used in cycles
    if engine == "CYCLES":
        use_noise_threshold = settings.get("use_noise_threshold")
        noise_threshold = settings.get("noise_threshold")
        if use_noise_threshold is not None:
            bpy.context.scene.cycles.use_adaptive_sampling = use_noise_threshold
            if use_noise_threshold:
                bpy.context.scene.cycles.adaptive_threshold = float(noise_threshold)


def _exists(lib):
    """
    return False iff file Blender has ref to is external and doesn't exist, True otherwise
    """
    try:
        if isinstance(lib, bpy.types.MovieClip):
            path = lib.filepath
            if path != '' and not os.path.exists(bpy.path.abspath(path).encode('utf-8')):
                return False
        # if it's an external file
        if lib.packed_file is None:
            path = lib.filepath
            # original file removed the relative path slashes, which could ruin missing file detection
            # if path.startswith('//'):
            #     path = path[2:]
            if path != '' and not os.path.exists(bpy.path.abspath(path).encode('utf-8')):
                return False
    except AttributeError:
        pass
    
    return True


def _get_missing():
    """
    find and return all missing files and caches that the .blend can't find
    return missing_files, missing_caches
    """
    # NOTE: partially duplicated in blender scanner fargate and _set_found_elsewhere() in this file
    missing_files = []
    for lib in bpy.data.libraries:
        if not _exists(lib):
            missing_files.append(lib.filepath)
    for lib in bpy.data.images:
        if not _exists(lib):
            missing_files.append(lib.filepath)
    for lib in bpy.data.movieclips:
        if not _exists(lib):
            missing_files.append(lib.filepath)

    known_sims = {'PARTICLE_SYSTEM', 'CLOTH', 'COLLISION', 'SOFT_BODY', 'DYNAMIC_PAINT', 'FLUID'}
    missing_caches = set()
    for obj in bpy.context.scene.objects:
        modifiers = obj.modifiers
        for mod in modifiers:
            if mod.type in known_sims:
                # check for missing baked caches
                try:
                    path = mod.domain_settings.cache_directory
                    # original file removed the relative path slashes, which could ruin missing file detection
                    # if path.startswith('//'):
                    #     path = path[2:]
                    if path != '' and not os.path.exists(bpy.path.abspath(path).encode('utf-8')):
                        missing_caches.add(mod.domain_settings.cache_directory)
                except AttributeError:
                    pass

    return missing_files, list(missing_caches)


def _get_found_elsewhere(missing_files, missing_caches, zip_path):
    """
    return missing files and caches that were found at another path under the same name
    """
    missing_file_names = set([os.path.basename(path) for path in missing_files])
    missing_cache_names = set([os.path.basename(path) for path in missing_caches])
    found_missing_file_names_to_paths = {}
    found_missing_cache_names_to_paths = {}
    # iterate over existing files in "zipped_files" dir; does nothing if no zipped_files found, ie not a zip
    for root, existing_dirs, existing_files in os.walk(zip_path):
        for existing_file in existing_files:
            if existing_file in missing_file_names:
                missing_file_names.remove(existing_file)
                found_missing_file_names_to_paths[existing_file] = os.path.join(root, existing_file)

        for existing_dir in existing_dirs:
            if existing_dir in missing_cache_names:
                missing_cache_names.remove(existing_dir)
                found_missing_cache_names_to_paths[existing_dir] = os.path.join(root, existing_dir)

    return found_missing_file_names_to_paths, found_missing_cache_names_to_paths


def _set_found_elsewhere(found_missing_file_names_to_paths, found_missing_cache_names_to_paths):
    """
    set missing file/cache paths to paths of newly found files/dirs with same name
    """
    # NOTE: partially duplicated in blender scanner and _get_missing() in this file
    for lib in bpy.data.libraries:
        if not _exists(lib):
            found_path = found_missing_file_names_to_paths.get(os.path.basename(lib.filepath))
            if found_path:
                lib.filepath = found_path
    for lib in bpy.data.images:
        if not _exists(lib):
            found_path = found_missing_file_names_to_paths.get(os.path.basename(lib.filepath))
            if found_path:
                lib.filepath = found_path
    for lib in bpy.data.movieclips:
        if not _exists(lib):
            found_path = found_missing_file_names_to_paths.get(os.path.basename(lib.filepath))
            if found_path:
                lib.filepath = found_path

    known_sims = {'PARTICLE_SYSTEM', 'CLOTH', 'COLLISION', 'SOFT_BODY', 'DYNAMIC_PAINT', 'FLUID'}
    for obj in bpy.context.scene.objects:
        modifiers = obj.modifiers
        for mod in modifiers:
            if mod.type in known_sims:
                # check for missing baked caches
                try:
                    path = mod.domain_settings.cache_directory
                    # original file removed the relative path slashes, which could ruin missing file detection
                    # if path.startswith('//'):
                    #     path = path[2:]
                    if path != '' and not os.path.exists(bpy.path.abspath(path).encode('utf-8')):
                        found_path = found_missing_cache_names_to_paths.get(os.path.basename(path))
                        if found_path:
                            mod.domain_settings.cache_directory = found_path
                except AttributeError:
                    pass


def modify_missing_found_elsewhere():
    """
    searches blend directory contents to see if missing files and caches by the same name are found at a different path
    if found, sets blender filepath to the new path
    NOTE: partially duplicated in blender scanner fargate
    """
    current_path = bpy.data.filepath
    zip_path = None
    # find where user files/dirs are in cache
    while current_path and current_path != "/" and current_path != CACHE_DIR:
        zip_path = current_path
        current_path = os.path.dirname(current_path)
    
    missing_files, missing_caches = _get_missing()
    found_missing_file_names_to_paths, found_missing_cache_names_to_paths = _get_found_elsewhere(missing_files, missing_caches, zip_path)
    _set_found_elsewhere(found_missing_file_names_to_paths, found_missing_cache_names_to_paths)


def main():
    modify_settings_with_overrides()
    modify_missing_found_elsewhere()
    # ensure changes are persistent
    bpy.ops.wm.save_mainfile()


if __name__=="__main__":
    main()
