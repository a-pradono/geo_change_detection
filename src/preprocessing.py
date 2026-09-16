"""AOI clipping, reflectance scaling, clipping, and radiometric normalization.

Exact logic validated interactively in the notebook, with AOI clipping added
at the very start (before any statistics are computed), and every downstream
step made NaN-safe so AOI-excluded pixels never influence percentiles or
mean/std used elsewhere in the chain:
  0. apply_aoi_mask: pixels outside the AOI polygon are set to NaN across all
     bands, applied to both "before" and "after" immediately after stacking,
     BEFORE reflectance scaling.
  1. normalize_reflectance: divide by 10000 if the 99th percentile > 1.5
     (raw uint16 digital numbers), else leave the array as-is. Uses
     np.nanpercentile so AOI-excluded (NaN) pixels don't affect the check.
  2. Clip to [0, 1] -- applied AFTER reflectance scaling, BEFORE radiometric
     normalization. np.clip is already NaN-safe (NaN passes through
     unchanged), so no logic change was needed here, just confirming it
     still runs after AOI masking.
  3. match_stats: linear mean/std rescale of `source` onto `reference`,
     applied per band, using np.nanmean/np.nanstd so AOI-excluded pixels
     don't skew the statistics.
"""
import geopandas as gpd
import numpy as np
import rasterio.features


def apply_aoi_mask(stacked_array: np.ndarray, aoi_path, transform, crs):
    """Clip `stacked_array` to the AOI polygon at `aoi_path`.

    Loads the AOI polygon, reprojects it to match the raster CRS, builds a
    boolean mask (True = inside AOI), and sets all out-of-AOI pixels to NaN
    across all bands.

    Parameters
    ----------
    stacked_array : np.ndarray
        (H, W, bands) float array (must be float, since NaN requires it).
    aoi_path : str or Path
        Path to the AOI GeoJSON/vector file.
    transform : affine.Affine
        The raster's transform (for rasterizing the AOI onto the same grid).
    crs :
        The raster's CRS (the AOI is reprojected to match, not the other
        way around).

    Returns
    -------
    (masked_array, aoi_mask, aoi_gdf_native) : (np.ndarray, np.ndarray, GeoDataFrame)
        masked_array: `stacked_array` with out-of-AOI pixels set to NaN.
        aoi_mask: (H, W) bool, True = inside AOI.
        aoi_gdf_native: the AOI GeoDataFrame reprojected into `crs`.
    """
    aoi_gdf = gpd.read_file(aoi_path)
    aoi_native = aoi_gdf.to_crs(crs)
    aoi_mask = rasterio.features.geometry_mask(
        aoi_native.geometry, out_shape=stacked_array.shape[:2],
        transform=transform, invert=True
    )
    masked = stacked_array.copy()
    masked[~aoi_mask] = np.nan
    return masked, aoi_mask, aoi_native


def normalize_reflectance(img: np.ndarray) -> np.ndarray:
    """Scale raw digital numbers to reflectance (0-1) if needed.

    If the 99th percentile of `img` exceeds 1.5, the array is assumed to be
    raw scaled digital numbers (typical Sentinel-2 L2A: reflectance * 10000)
    and is divided by 10000. Otherwise it is returned unchanged.

    Uses np.nanpercentile so AOI-excluded (NaN) pixels don't affect the check.
    """
    img = img.copy()
    if np.nanpercentile(img, 99) > 1.5:
        img = img / 10000.0
    return img


def clip_reflectance(img: np.ndarray) -> np.ndarray:
    """Clip reflectance values to the valid [0, 1] range.

    np.clip is NaN-safe: NaN inputs pass through as NaN unchanged, so
    AOI-excluded pixels stay excluded rather than being clamped to 0 or 1.
    """
    return np.clip(img, 0, 1)


def match_stats(source: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Linearly rescale `source` (a single band, 2D) onto `reference`'s
    mean/std, so the two dates are radiometrically comparable band-by-band.

    Uses np.nanmean/np.nanstd so AOI-excluded pixels don't skew the
    statistics; NaN positions in `source` remain NaN in the output (NaN
    propagates through the arithmetic).
    """
    s_mean, s_std = np.nanmean(source), np.nanstd(source) + 1e-8
    r_mean, r_std = np.nanmean(reference), np.nanstd(reference) + 1e-8
    return (source - s_mean) * (r_std / s_std) + r_mean


def preprocess(
    before_raw: np.ndarray,
    after_raw: np.ndarray,
    aoi_path,
    transform,
    crs,
) -> tuple:
    """Run the full preprocessing chain on a (before, after) pair of
    (H, W, bands) arrays, in the validated order:
    AOI clip -> reflectance scale -> clip -> radiometric normalize
    (after onto before).

    Parameters
    ----------
    before_raw, after_raw : np.ndarray
        (H, W, bands) raw arrays (any numeric dtype; cast to float32 here).
    aoi_path : str or Path
        Path to the AOI GeoJSON/vector file.
    transform, crs :
        The raster's transform/CRS (must match between before_raw/after_raw).

    Returns
    -------
    (before, after_norm, aoi_mask, aoi_gdf_native)
        before, after_norm : float32, band-normalized `after` matched onto
            `before`'s statistics per band. NaN outside the AOI.
        aoi_mask : (H, W) bool, True = inside AOI.
        aoi_gdf_native : the AOI GeoDataFrame reprojected into `crs`.
    """
    before = before_raw.astype(np.float32)
    after = after_raw.astype(np.float32)

    # AOI clipping runs first, on both dates, before any statistic is
    # computed, so out-of-AOI pixels never influence percentiles/PCA/Otsu.
    before, aoi_mask, aoi_native = apply_aoi_mask(before, aoi_path, transform, crs)
    after[~aoi_mask] = np.nan  # reuse the same mask -- avoids re-reading/rasterizing the AOI twice

    before = normalize_reflectance(before)
    after = normalize_reflectance(after)

    before = clip_reflectance(before)
    after = clip_reflectance(after)

    after_norm = after.copy()
    n_bands = before.shape[2]
    for b in range(n_bands):
        after_norm[:, :, b] = match_stats(after[:, :, b], before[:, :, b])

    return before, after_norm, aoi_mask, aoi_native
