# EcoSentinel AI

Satellite early warning for illegal mining in Ghana's forests. This is a working prototype built on real Sentinel-2 satellite images.

**Live dashboard:** https://ecosentinel-ai-gh.netlify.app

**Pilot area:** Obuasi and Amansie West, Ashanti Region, Ghana (281 km²). Images from 31 January 2023 and 25 January 2026, both in the dry season so the two dates can be compared fairly.

**Concept and proposal:** add the proposal author's name here.

## What it does

EcoSentinel compares two satellite images of the same ground, flags places where dense vegetation turned into bare ground, groups the flags into hotspots, and ranks them by size. A person then verifies each hotspot. Nothing is acted on automatically.

## Results on the pilot area

| Measure | Value |
|---|---|
| New bare ground, Jan 2023 to Jan 2026 | about 624 ha (3.6% of the vegetation that was there) |
| Hotspots flagged | 192 (30 bigger than 5 ha, 4 bigger than 20 ha) |
| Share of the clearing in the 10 biggest hotspots | 42% |
| Share of the area that could be checked | 87.7% (the rest was under cloud or shadow in 2023) |
| Largest hotspot | 103 ha, right beside the licensed mine, so most likely a mine expansion |

The 2026 image with the detected change in red is in `results/change_map.png`. The ranked hotspot list is in `results/hotspots.csv`.

## How it works

1. **Detect.** For every 10 m pixel, a vegetation index (NDVI) is calculated on both dates. A pixel is flagged when it was dense vegetation in 2023 (NDVI 0.55 or more), is bare ground in 2026 (NDVI 0.30 or less), and the index fell by at least 0.30. Clouds and their shadows are masked out first, using a simple colour and brightness rule.
2. **Assess.** Flagged patches smaller than about 0.24 ha are dropped. Patches within about 150 m of each other become one hotspot. Hotspots are ranked by area, and places that were already mostly open ground in 2023 (town, large mine) are reported separately.
3. **Verify.** The dashboard tracks the human check for each hotspot: queued for a field visit, confirmed illegal, licensed or other, or false alarm.

## The dashboard

`index.html` is one self-contained file: the satellite images, the change layer and the data are all inside it. Open it in a browser, or host it anywhere that serves static files. It has a before and after slider, a vegetation-health view, pins for the 12 biggest hotspots with before and after pictures, and the verification queue (saved in the browser).

## What is in this repo

```
index.html                       the finished dashboard (one file, open it in a browser)
dashboard/index.template.html    the readable source of the dashboard: page, styles and scripts
dashboard/build_dashboard.py     runs the detection and builds index.html from two satellite scenes
analysis/detect_change.py        the change detection (clouds, vegetation index, hotspots)
results/                         change map, ranked hotspots and summary from the pilot run
```

## Dashboard source

`dashboard/index.template.html` is the code you read and edit. It has one placeholder where the data goes. `dashboard/build_dashboard.py` runs the detection, prepares the pictures and numbers, and writes the finished single-file page:

```bash
python dashboard/build_dashboard.py --old data/2023 --new data/2026 --out index.html
```

Use `--mine-rank N` to mark which hotspot sits beside a licensed mine, so the page shows a licence-check note for it. The words on the page (place name, dates, notes) are written for the Obuasi pilot, so edit the template to change them for another area.

## Reproduce the analysis

1. Open the [Copernicus Browser](https://browser.dataspace.copernicus.eu) and create a free account.
2. Choose Sentinel-2 **L2A**. Draw the same box for both dates and pick a dry-season image with little cloud for each.
3. Open the download tool, go to the **Analytical** tab, and choose: format **TIFF (32-bit float)**, coordinate system **WGS 84 (EPSG:4326)**, resolution **High**. Under Raw, tick **B04** and **B08**. Also tick **True color** under Visualised.
4. Unzip each download into its own folder, for example `data/2023` and `data/2026`.
5. Run:

```bash
pip install -r analysis/requirements.txt
python analysis/detect_change.py --old data/2023 --new data/2026 --out results
```

The settings are listed at the top of `analysis/detect_change.py`.

## Limits

- It shows land change, not who did it or whether it is legal. Telling legal from illegal needs the boundaries of licensed mines.
- 12% of the area was under cloud or shadow in 2023 and could not be checked, so the real total is probably higher.
- Only two dates are compared, so it cannot show when the clearing happened.
- The cloud filter is a simple rule, checked by eye. Accuracy has not yet been tested against field-verified sites.

## Data and credits

Contains modified Copernicus Sentinel data (2023, 2026). Sentinel-2 L2A imagery from the European Space Agency and the Copernicus programme, free and open.

