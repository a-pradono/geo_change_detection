"""PCA/CVA change magnitude computation and Otsu thresholding.

Exact logic validated interactively in the notebook:
  - diff_img = after_norm - before
  - band_diff_mag = ||diff_img||  (L2 norm across bands) -- this is
    numerically identical to the CVA magnitude below.
  - PCA on the flattened diff_img with n_components == number of bands;
    pca_mag = L2 norm across all PCA components.
  - Otsu threshold (skimage.filters.threshold_otsu) on pca_mag to get the
    binary change mask.
  - CVA direction (optional, for change-type characterization): PCA to 2
    components, np.arctan2 of the 2 projected components.

NaN-safe restructuring for AOI clipping: `before`/`after_norm` now contain
NaN outside the AOI (see preprocessing.apply_aoi_mask). PCA and Otsu cannot
accept NaN, so:
  1. Flatten the diff image; valid_rows = ~np.isnan(flat).any(axis=1).
  2. Fit PCA only on flat[valid_rows].
  3. Compute the Otsu threshold only on the valid (non-NaN) magnitude values.
  4. Scatter the valid-only results back into full-size (H, W) images via the
     valid_rows index, filling excluded positions with NaN (for the
     continuous magnitude/direction images).
  5. The binary mask is built as an all-zero array, with the thresholded
     comparison filled in only at valid_rows positions -- so out-of-AOI
     pixels are 0 ("no change") in the mask, never NaN.
"""
from dataclasses import dataclass

import numpy as np
from skimage.filters import threshold_otsu
from sklearn.decomposition import PCA


@dataclass
class ChangeDetectionResult:
    diff_img: np.ndarray            # (H, W, bands) after_norm - before; NaN outside AOI
    band_diff_mag: np.ndarray       # (H, W) == CVA magnitude; NaN outside AOI
    pca_mag: np.ndarray             # (H, W) L2 norm across all PCA components; NaN outside AOI
    pca_explained_variance: np.ndarray
    pca_threshold: float
    pca_mask: np.ndarray            # (H, W) uint8, 0/1, 0 outside AOI (never NaN)
    cva_dir: np.ndarray             # (H, W) arctan2 of first 2 PCA components; NaN outside AOI
    valid_pixel_count: int          # number of in-AOI pixels (denominator for % calcs)


def compute_change(before: np.ndarray, after_norm: np.ndarray) -> ChangeDetectionResult:
    """Compute PCA/CVA change magnitude and an Otsu-thresholded binary mask.

    Parameters
    ----------
    before, after_norm : np.ndarray
        (H, W, bands) float arrays, already preprocessed (see
        preprocessing.preprocess). NaN outside the AOI.
    """
    diff_img = after_norm - before
    # NaN propagates automatically through the norm wherever any band is
    # NaN, so band_diff_mag is NaN outside the AOI with no special handling.
    band_diff_mag = np.linalg.norm(diff_img, axis=2)

    h, w, n_bands = diff_img.shape
    flat = diff_img.reshape(-1, n_bands)
    valid_rows = ~np.isnan(flat).any(axis=1)
    flat_valid = flat[valid_rows]
    valid_pixel_count = int(valid_rows.sum())

    pca = PCA(n_components=n_bands)
    valid_pcs = pca.fit_transform(flat_valid)
    valid_mag = np.linalg.norm(valid_pcs, axis=1)

    pca_mag = np.full(h * w, np.nan, dtype=np.float32)
    pca_mag[valid_rows] = valid_mag
    pca_mag = pca_mag.reshape(h, w)

    pca_thresh = threshold_otsu(valid_mag)

    pca_mask_flat = np.zeros(h * w, dtype=np.uint8)
    pca_mask_flat[valid_rows] = (valid_mag > pca_thresh).astype(np.uint8)
    pca_mask = pca_mask_flat.reshape(h, w)

    pca2 = PCA(n_components=2)
    valid_proj = pca2.fit_transform(flat_valid)
    valid_dir = np.arctan2(valid_proj[:, 1], valid_proj[:, 0])

    cva_dir = np.full(h * w, np.nan, dtype=np.float32)
    cva_dir[valid_rows] = valid_dir
    cva_dir = cva_dir.reshape(h, w)

    return ChangeDetectionResult(
        diff_img=diff_img,
        band_diff_mag=band_diff_mag,
        pca_mag=pca_mag,
        pca_explained_variance=pca.explained_variance_ratio_,
        pca_threshold=float(pca_thresh),
        pca_mask=pca_mask,
        cva_dir=cva_dir,
        valid_pixel_count=valid_pixel_count,
    )
