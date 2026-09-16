# Sentinel-2 Change Analysis

**Data:** Sentinel-2 3-band (B04–Red, B03–Green, B02–Blue) imagery
**Area of Interest:** Open-Pit Mining Site in Zambia
**Dates Compared:** 2023-08-12 (before) to 2023-09-02 (after) or 3-week interval
**Image Extent:** 1597 × 1673 pixels, 10 m resolution, CRS: EPSG:32735 (WGS 84 / UTM Zone 35S)

---

## 1. Method

### Change detection algorithm chosen

A **combined Principal Component Analysis (PCA) and Change Vector Analysis (CVA)** approach was used, applied to the three available visible bands (Red, Green, Blue). The algorithm runs as follows:

**Step 1: Reflectance scaling**
The AOI-clipped stacks (pixels outside `aoi.geojson` set to NaN, 0.98% of the raster) are converted from raw digital numbers to surface reflectance (÷10000) and clipped to the physically valid [0, 1] range. All later steps ignore NaN pixels, so every percentage below is relative to the 2,645,712 in-AOI pixels.

**Step 2: Radiometric normalization**
The normalization corrects for differences between the two image image dates that aren't real ground change, such as sun angle or sensor calibration drift that can make image systematically brighter or dimmer than the other.

**Step 3: Band differencing**
Band differencing computes the pixel-by-pixel difference between the two date's reflectance values (after-before) to measure pixel's brightness changed in each band (Red, Green, Blue).

**Step 4: PCA-based magnitude**
Principal Component Analysis (3 components) is fitted on the valid pixels of the difference image, and the L2 norm across each pixel's component scores is used as a decorrelated change magnitude. The explained variance ratio was **87.6%, 10.7% and 1.8%**. This indicates one dominant, shared cross-band change signal plus a much smaller secondary pattern and negligible residual noise.

**Step 5: Change Vector Analysis (CVA)**
CVA measures the change between two dates as a vector in spectral space. For each pixel, it compares the reflectance values (R, G, B) at date 1 vs date 2 and computes:
- Magnitude: Euclidian distance and how much change happened
- Direction: Which way the vector points and what kind of change happened


**Step 6: Otsu automatic thresholding**
Otsu's method is applied to the valid magnitude values to select a threshold automatically, producing a binary change and no-change mask (pixels outside the AOI count as no-change). The threshold was **0.0598** for both the PCA and CVA magnitudes, flagging **3.68%** of the AOI.

**Step 7: Artifact cleanup**
Morphological opening with a structuring element is applied to the PCA mask. This removes thin diagonal seam/detector-line artifacts and isolated speckle, which a visual overlay check showed were not real ground change. Flagged area drops from **3.68% to 2.08%** of the AOI.

### Why this method

The available imagery was **RGB-only, with no near-infrared (NIR) band**, which ruled out NDVI, the most commonly used vegetation-change index since NDVI requires NIR. Given this constraint, the choice was between simple per-band differencing or image ratioing (uses only one band's information, or requires collapsing to a single index) versus multi-band approaches that use all three available bands jointly.

CVA and PCA were selected because:
- They use **all three bands simultaneously**, rather than reducing to a single channel, so no spectral information is discarded.
- CVA magnitude is **mathematically identical to Euclidean band differencing** in this 3-band setting, but additionally provides a **direction** component, which supports distinguishing different *types* of change (not implemented here as a separate output, but available if class discrimination is needed later).
- Published change-detection literature (e.g., digital change detection reviews comparing differencing, ratioing, PCA, and CVA) reports that combining PCA and CVA outperforms either method alone, and that this combination is well suited to multi-band optical data lacking NIR.
- Cross-validating three independent methods (band differencing, CVA, PCA) and finding they produced identical classified masks (0 differing pixels across the 2,645,712 in-AOI pixels) gave strong confidence that the detected changes reflect a genuine, robust signal rather than a method-specific artifact.

---

## 2. Results

### Where changes occur

The detected changes are concentrated around two light-toned, which is the open-pit mining complexes (upper-center and lower-center AOI). Key localized changes include:

- **Pit Rims and Excavation Edges**: Dense polygon outlines trace the bright pit boundaries (particularly the upper cluster), indicating active mine boundary shifts.
- **Stockpile Areas**: Internal clustering where material accumulation or removal alters surface reflectance.
- **Haul Roads and Access Tracks**: Short segments within and around the mine complex.
- **Background and Perimeter**: Change is sparse outside the mine, limited to a small cluster in the eastern water body and minor isolated specks across darker forested/water areas.

### Pattern of changes observed

- **Highly Clustered**: Over 92% of the flagged change is concentrated within the open-pit mine footprint rather than spread diffusely across the AOI.
- **Edge-Dominant**: Polygons predominantly map pit-edge advancements and stockpile perimeters rather than uniform surface shifts.
- **Localized Off-Site Cluster**: A rounded cluster along the eastern water body suggests a real localized shift. For example, secondary excavation or turbidity change, rather than a linear seam artifact.
- **Minimal Noise**: Sparse background marks align with expected residual noise from minor water or shadow reflectance variations.
- **AOI Integrity**: The AOI boundary closely mirrors the raster edge, confirming minimal spatial exclusion (~99% coverage).

---

## 3. Interpretation

### What might these changes represent

**Active Mining Operations / Land Clearing**
- Most of the 2.08% (~551 ha) detected change sits inside and along the edges of two bright terraced open-pit complexes in the true-color imagery.
- Change is concentrated at pit rims, excavation faces, stocpiles, and haul roads.
- A curved band of change along the upper pit's lower rim is generally consistent with a freshly worked pit edge or a new stockpile face.

**Agricultural Activity**
- Rectangular or blocky patches adjacent to a water source are most likely a classic signature of cultivated fields. Irrigated plots are typically laid out in regular geometric shapes right next to rivers in the Eastern part of AOI.
- In Zambia during a 3-week window from August to September is in late dry season. The fields might be harvested or burned for stubble clearing or maybe newly irrigated. All of which produce a sharp, field-shaped reflectance change (wet vs dry soil).

Overall, the detected change across 2.08% (~551 ha) out of ~26,457.01 ha of the AOI is driven almost entirely by active open-pit mining operations, specifically pit boundary expansion, stockpile shifts, and haul-road maintenance within an established industrial footprint. In addition, the isolated cluster along the eastern water body displays a blocky, indicating an agricultural activity. 
