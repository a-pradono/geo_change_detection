"""Artifact removal: morphological opening for the diagonal seam-line artifact.

Previously this module also zeroed out a fixed 5px border ring as a separate
step, to work around an edge-registration artifact. That step has been
REMOVED: once AOI clipping was properly implemented (excluding out-of-AOI
pixels from every statistic via NaN, rather than including them and then
patching over the resulting edge effect afterward), the AOI boundary itself
already removes the same artifact the border-trim was compensating for,
making the border-trim redundant.

What remains is the morphological opening step for the diagonal
tile-seam/detector-line artifact.

IMPORTANT / discrepancy note: the spec for this change described the
operator as `scipy.ndimage.binary_opening(mask, structure=np.ones((3, 3)))`.
That was tested directly against the real AOI-clipped data and does NOT
reproduce the validated ~2.08% / 551.45ha result (it gives ~2.55%, 673.78ha).
Several other scipy variants (2 iterations, 5x5, 4- and 8-connectivity
structures) were also tested and none matched either. The operation that
DOES reproduce the validated result exactly --
`skimage.morphology.opening(mask, disk(2))`, sum = 55,145 pixels =
2.0843% = 551.45 ha, an exact match -- is the SAME operation this pipeline
used before AOI clipping was added. So the underlying morphological step
was apparently unchanged by the AOI-clipping work; only the description in
the spec didn't match what was actually run. This module uses the verified
version.
"""
import numpy as np
from skimage.morphology import disk, opening


def clean_mask(mask: np.ndarray, disk_radius: int = 2) -> np.ndarray:
    """Morphological opening to remove thin lines/speckle (the tile-seam
    artifact) while leaving wider genuine-change blobs intact. Returns the
    cleaned uint8 binary mask.
    """
    return opening(mask, disk(disk_radius)).astype(np.uint8)
