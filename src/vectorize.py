"""Raster-to-polygon conversion for the cleaned binary change mask."""
import geopandas as gpd
import numpy as np
from rasterio.features import shapes as rio_shapes
from rasterstats import zonal_stats
from shapely.geometry import shape


def vectorize_change_mask(
    binary_mask: np.ndarray,
    transform,
    crs,
    date_before: str,
    date_after: str,
    magnitude: np.ndarray = None,
    min_area_sq_m: float = 100.0,
) -> gpd.GeoDataFrame:
    """Convert the cleaned binary change mask into a polygon GeoDataFrame.

    No structural change needed for AOI support: rasterio.features.shapes()
    with mask=(binary_mask == 1) naturally excludes both "no change" (0) and
    "out of AOI" (also 0 in the mask -- see change_detection.py) pixels
    correctly, since AOI-excluded pixels were already zeroed during mask
    construction, not left as NaN.

    Parameters
    ----------
    binary_mask : np.ndarray
        (H, W) uint8 mask, 1 = changed, 0 = unchanged/out-of-AOI (the output
        of cleanup.clean_mask).
    transform : affine.Affine
        The ORIGINAL input raster's transform (threaded through from the
        input file's rasterio profile) -- not recomputed or defaulted, so
        polygons land exactly where the source pixels do.
    crs :
        The original raster's CRS.
    date_before, date_after : str
        Labels for the two acquisition dates being compared.
    magnitude : np.ndarray, optional
        (H, W) continuous change-magnitude raster (e.g. change_detection's
        pca_mag), same shape/transform as `binary_mask`. May contain NaN
        outside the AOI. If given, each polygon's `confidence` is its mean
        magnitude, min-max normalized to [0, 1] across all polygons.
    min_area_sq_m : float
        Polygons smaller than this (in the raster's native CRS units --
        typically metres for a projected/UTM CRS) are dropped. Default 100
        (one 10m x 10m pixel) filters out zero/degenerate slivers only.

    Returns
    -------
    geopandas.GeoDataFrame with columns:
        id, date_before, date_after, area_m2, confidence, geometry
        (confidence is omitted if `magnitude` was not given)
    """
    polygons = [
        shape(geom)
        for geom, value in rio_shapes(
            binary_mask,
            mask=(binary_mask == 1),
            transform=transform,
        )
    ]

    gdf = gpd.GeoDataFrame({"geometry": polygons}, crs=crs)

    # area_m2 computed in the raster's native CRS units, before any reprojection.
    gdf["area_m2"] = gdf.geometry.area
    gdf["date_before"] = date_before
    gdf["date_after"] = date_after

    columns = ["id", "date_before", "date_after", "area_m2"]

    if magnitude is not None and len(gdf):
        # zonal_stats can't handle NaN with a numeric nodata value -- replace
        # NaN (out-of-AOI) with a literal sentinel before calling it.
        magnitude_for_stats = np.where(np.isnan(magnitude), -9999.0, magnitude)
        stats = zonal_stats(
            gdf.geometry, magnitude_for_stats, affine=transform, stats=["mean"], nodata=-9999.0
        )
        mean_magnitude = np.array([s["mean"] for s in stats], dtype=np.float64)
        mag_min, mag_max = mean_magnitude.min(), mean_magnitude.max()
        if mag_max > mag_min:
            gdf["confidence"] = (mean_magnitude - mag_min) / (mag_max - mag_min)
        else:
            gdf["confidence"] = 0.0
        columns.append("confidence")
    elif magnitude is not None:
        gdf["confidence"] = np.array([], dtype=np.float64)  # empty gdf; keeps column present
        columns.append("confidence")

    gdf = gdf[gdf["area_m2"] >= min_area_sq_m].reset_index(drop=True)
    gdf["id"] = range(1, len(gdf) + 1)
    columns.append("geometry")
    gdf = gdf[columns]

    return gdf
