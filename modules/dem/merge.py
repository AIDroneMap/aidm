import math
import numpy as np
from scipy import ndimage
import rasterio
from rasterio.transform import Affine, rowcol
from modules import system
from modules.dem.commands import compute_euclidean_map
from modules import log
from modules import io
import os
import threading
from modules.concurrency import parallel_map

def euclidean_merge_dems(input_dems, output_dem, creation_options={}, euclidean_map_source=None, max_workers=1):
    """
    Based on https://github.com/mapbox/rio-merge-rgba
    and ideas from Anna Petrasova
    implementation by Piero Toffanin

    Computes a merged DEM by computing/using a euclidean 
    distance to NODATA cells map for all DEMs and then blending all overlapping DEM cells 
    by a weighted average based on such euclidean distance.

    Output blocks are independent, so they are computed in parallel
    with max_workers threads. Blocks are still written in block order,
    so the output file is byte-identical to a serial run. With
    max_workers=1 (the default) everything runs in the calling thread.
    """
    inputs = []
    bounds=None
    precision=7

    existing_dems = []
    for dem in input_dems:
        if not io.file_exists(dem):
            log.WARNING("%s does not exist. Will skip from merged DEM." % dem)
            continue
        existing_dems.append(dem)

    if len(existing_dems) == 0:
        log.WARNING("No input DEMs, skipping euclidean merge.")
        return

    with rasterio.open(existing_dems[0]) as first:
        src_nodata = first.nodatavals[0]
        res = first.res
        dtype = first.dtypes[0]
        profile = first.profile
        dst_count = first.count

    for dem in existing_dems:
        eumap = compute_euclidean_map(dem, io.related_file_path(dem, postfix=".euclideand", replace_base=euclidean_map_source), overwrite=False)
        if eumap and io.file_exists(eumap):
            inputs.append((dem, eumap))

    log.INFO("%s valid DEM rasters to merge" % len(inputs))

    sources = [(rasterio.open(d), rasterio.open(e)) for d,e in inputs]

    # Extent from option or extent of all inputs.
    if bounds:
        dst_w, dst_s, dst_e, dst_n = bounds
    else:
        # scan input files.
        # while we're at it, validate assumptions about inputs
        xs = []
        ys = []
        for src_d, src_e in sources:
            left, bottom, right, top = src_d.bounds
            xs.extend([left, right])
            ys.extend([bottom, top])
            if src_d.profile["count"] != 1 or src_e.profile["count"] != 1:
                raise ValueError("Inputs must be 1-band rasters")
        dst_w, dst_s, dst_e, dst_n = min(xs), min(ys), max(xs), max(ys)

    # The scan handles are not used again. Workers open their own.
    for src_d, src_e in sources:
        src_d.close()
        src_e.close()
    log.INFO("Output bounds: %r %r %r %r" % (dst_w, dst_s, dst_e, dst_n))

    output_transform = Affine.translation(dst_w, dst_n)
    output_transform *= Affine.scale(res[0], -res[1])

    # Compute output array shape. We guarantee it will cover the output
    # bounds completely.
    output_width = int(math.ceil((dst_e - dst_w) / res[0]))
    output_height = int(math.ceil((dst_n - dst_s) / res[1]))

    # Adjust bounds to fit.
    dst_e, dst_s = output_transform * (output_width, output_height)
    log.INFO("Output width: %d, height: %d" % (output_width, output_height))
    log.INFO("Adjusted bounds: %r %r %r %r" % (dst_w, dst_s, dst_e, dst_n))

    profile["transform"] = output_transform
    profile["height"] = output_height
    profile["width"] = output_width
    profile["tiled"] = creation_options.get('TILED', 'YES') == 'YES'
    profile["blockxsize"] = creation_options.get('BLOCKXSIZE', 512)
    profile["blockysize"] = creation_options.get('BLOCKYSIZE', 512)
    profile["compress"] = creation_options.get('COMPRESS', 'LZW')
    profile["nodata"] = src_nodata

    # Creation opts
    profile.update(creation_options)

    # create destination file
    with rasterio.open(output_dem, "w", **profile) as dstrast:

        # GDAL handles are not thread-safe, so each worker thread opens
        # its own set of input handles on first use.
        tls = threading.local()
        opened_sources = []
        opened_lock = threading.Lock()

        def get_sources():
            srcs = getattr(tls, "sources", None)
            if srcs is None:
                srcs = [(rasterio.open(d), rasterio.open(e)) for d, e in inputs]
                tls.sources = srcs
                with opened_lock:
                    opened_sources.append(srcs)
            return srcs

        def compute_block(dst_window, bounds):
            """Return the merged array for one output block (no write)."""
            left, bottom, right, top = bounds
            dst_rows, dst_cols = (dst_window.height, dst_window.width)

            # initialize array destined for the block
            dst_shape = (dst_count, dst_rows, dst_cols)

            dstarr = np.zeros(dst_shape, dtype=dtype)
            distsum = np.zeros(dst_shape, dtype=dtype)
            small_distance = 0.001953125

            for src_d, src_e in get_sources():
                # The full_cover behavior is problematic here as it includes
                # extra pixels along the bottom right when the sources are
                # slightly misaligned
                #
                # src_window = get_window(left, bottom, right, top,
                #                         src.transform, precision=precision)
                #
                # With rio merge this just adds an extra row, but when the
                # imprecision occurs at each block, you get artifacts

                nodata = src_d.nodatavals[0]

                # Alternative, custom get_window using rounding
                src_window_d = tuple(zip(rowcol(
                        src_d.transform, left, top, op=round, precision=precision
                    ), rowcol(
                        src_d.transform, right, bottom, op=round, precision=precision
                    )))

                src_window_e = tuple(zip(rowcol(
                        src_e.transform, left, top, op=round, precision=precision
                    ), rowcol(
                        src_e.transform, right, bottom, op=round, precision=precision
                    )))

                temp_d = np.zeros(dst_shape, dtype=dtype)
                temp_d = src_d.read(
                    out=temp_d, window=src_window_d, boundless=True, masked=False
                )

                temp_e = np.zeros(dst_shape, dtype=dtype)
                temp_e = src_e.read(
                    out=temp_e, window=src_window_e, boundless=True, masked=False
                )

                # Set NODATA areas in the euclidean map to a very low value
                # so that:
                #  - Areas with overlap prioritize DEM layers' cells that 
                #    are far away from NODATA areas
                #  - Areas that have no overlap are included in the final result
                #    even if they are very close to a NODATA cell
                temp_e[temp_e==0] = small_distance
                temp_e[temp_d==nodata] = 0

                np.multiply(temp_d, temp_e, out=temp_d)
                np.add(dstarr, temp_d, out=dstarr)
                np.add(distsum, temp_e, out=distsum)

            np.divide(dstarr, distsum, out=dstarr, where=distsum[0] != 0.0)

            # Perform nearest neighbor interpolation on areas where two or more rasters overlap
            # but where both rasters have only interpolated data. This prevents the creation
            # of artifacts that average areas of interpolation.
            indices = ndimage.distance_transform_edt(np.logical_and(distsum < 1, distsum > small_distance), 
                                                return_distances=False, 
                                                return_indices=True)
            dstarr = dstarr[tuple(indices)]

            dstarr[dstarr == 0.0] = src_nodata

            return dstarr

        # A tiled, compressed GeoTIFF stores blocks in the order they are
        # written. To get the same bytes as a serial run, blocks are written
        # in block order: a worker waits for its turn after computing.
        # parallel_map hands items out in order, so every lower index is
        # already held by a running worker and the wait cannot deadlock.
        block_windows = [(window, dstrast.window_bounds(window))
                         for _, window in dstrast.block_windows()]
        total_blocks = len(block_windows)

        turn = threading.Condition()
        state = {"next": 0, "error": None, "last_pct": -1}

        def process_block(item):
            # parallel_map hangs if this raises, so it never does. The
            # first error is stored and raised after all workers are done.
            index, (dst_window, bounds) = item
            dstarr = None

            with turn:
                failed = state["error"] is not None

            if not failed:
                try:
                    dstarr = compute_block(dst_window, bounds)
                except Exception as e:
                    with turn:
                        if state["error"] is None:
                            state["error"] = e

            with turn:
                while state["next"] != index:
                    turn.wait()
                failed = state["error"] is not None

            # Only the worker whose turn it is gets here, so the write
            # needs no lock.
            error = None
            if dstarr is not None and not failed:
                try:
                    dstrast.write(dstarr, window=dst_window)
                except Exception as e:
                    error = e

            with turn:
                if error is not None and state["error"] is None:
                    state["error"] = error
                state["next"] = index + 1
                pct = ((index + 1) * 100) // total_blocks
                if pct // 10 > state["last_pct"] // 10:
                    log.INFO("DEM merge [%d%%]" % pct)
                state["last_pct"] = pct
                turn.notify_all()

            dstarr = None

        try:
            parallel_map(process_block, list(enumerate(block_windows)),
                         max_workers=max_workers, single_thread_fallback=False)
        finally:
            for srcs in opened_sources:
                for src_d, src_e in srcs:
                    src_d.close()
                    src_e.close()

        if state["error"] is not None:
            raise state["error"]

    return output_dem
