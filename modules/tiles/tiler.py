import os
import sys
import math
import shutil
import threading
from modules import log
from modules.concurrency import max_workers_for_memory, parallel_map
from modules import system
from modules import io
from .static_tiler import StaticTiler

def generate_tiles(geotiff, output_dir, max_concurrency, resolution):
    circumference_earth_cm = 2*math.pi*637_813_700
    px_per_tile = 256
    resolution_equator_cm = circumference_earth_cm/px_per_tile
    zoom = math.ceil(math.log(resolution_equator_cm/resolution, 2))

    z_min = 5  # 4.89 km/px
    z_max = min(zoom, 23)  # No deeper zoom than 23 (1.86 cm/px at equator)

    if os.path.isdir(output_dir):
        shutil.rmtree(output_dir)
    
    log.INFO("Generating static tiles for %s" % geotiff)
    parent = StaticTiler(geotiff, output_dir, px_per_tile, tms=True)
    workers = []
    try:
        tiles = []
        for z in range(z_min, z_max + 1):
            tiles.extend(parent.get_tiles_for_zoom(z))

        # Statistics are computed once, here, and shared with the workers
        global_range = None
        if parent._ds.dtypes[0] != "uint8":
            global_range = parent.global_range()

        # Allow 8 MiB per 256 x 256 tile for conversion buffers and GDAL handles.
        max_workers = max_workers_for_memory(8 * 1024 * 1024, max_concurrency)
        log.INFO("Generating %s tiles with %s workers" % (len(tiles), max_workers))

        # GDAL datasets are not thread-safe: each thread opens its own
        local = threading.local()
        lock = threading.Lock()
        errors = []

        def process_tile(t):
            # Must not raise: parallel_map would hang with tiles left in the queue
            if errors:
                return
            tx, ty, tz = t
            try:
                tiler = getattr(local, "tiler", None)
                if tiler is None:
                    tiler = StaticTiler(geotiff, output_dir, px_per_tile, tms=True, global_range=global_range)
                    local.tiler = tiler
                    with lock:
                        workers.append(tiler)
                tiler.tile(tz, tx, ty)
            except Exception as e:
                with lock:
                    errors.append(e)

        parallel_map(process_tile, tiles, max_workers=max_workers)

        if errors:
            raise errors[0]
    finally:
        for tiler in workers:
            tiler.close()
        parent.close()

def generate_orthophoto_tiles(geotiff, output_dir, max_concurrency, resolution):
    try:
        generate_tiles(geotiff, output_dir, max_concurrency, resolution)
    except Exception as e:
        log.WARNING("Cannot generate orthophoto tiles: %s" % str(e))

def generate_colored_hillshade(geotiff):
    relief_file = os.path.join(os.path.dirname(__file__), "color_relief.txt")
    hsv_merge_script = os.path.join(os.path.dirname(__file__), "hsv_merge.py")
    colored_dem = io.related_file_path(geotiff, postfix="color")
    hillshade_dem = io.related_file_path(geotiff, postfix="hillshade")
    colored_hillshade_dem = io.related_file_path(geotiff, postfix="colored_hillshade")
    try:
        outputs = [colored_dem, hillshade_dem, colored_hillshade_dem]

        # Cleanup previous
        for f in outputs:
            if os.path.isfile(f):
                os.remove(f)

        system.run('gdaldem color-relief "%s" "%s" "%s" -alpha -co ALPHA=YES' % (geotiff, relief_file, colored_dem))
        system.run('gdaldem hillshade "%s" "%s" -z 1.0 -s 1.0 -az 315.0 -alt 45.0' % (geotiff, hillshade_dem))
        system.run('"%s" "%s" "%s" "%s" "%s"' % (sys.executable, hsv_merge_script, colored_dem, hillshade_dem, colored_hillshade_dem))
        
        return outputs
    except Exception as e:
        log.WARNING("Cannot generate colored hillshade: %s" % str(e))
        return (None, None, None)

def generate_dem_tiles(geotiff, output_dir, max_concurrency, resolution):
    try:
        colored_dem, hillshade_dem, colored_hillshade_dem = generate_colored_hillshade(geotiff)
        generate_tiles(colored_hillshade_dem, output_dir, max_concurrency, resolution)

        # Cleanup
        for f in [colored_dem, hillshade_dem, colored_hillshade_dem]:
            if os.path.isfile(f):
                os.remove(f)
    except Exception as e:
        log.WARNING("Cannot generate DEM tiles: %s" % str(e))
