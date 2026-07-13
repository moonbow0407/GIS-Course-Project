# Empty Map Canvas Design

## Goal

Use a blank map canvas in the GIS desktop platform until the user loads real spatial data.

## Scope

- Remove every mock cartographic element: background fill, coordinate grid, regions, roads, rivers, POIs, selection callout, scale bar, and north arrow.
- Keep the `QGraphicsScene` extent, pan, zoom, mouse-coordinate updates, and selection tools available for future loaded layers.
- Start with no selectable mock features and an empty selection count.

## Design

`MapCanvas` continues to own a `QGraphicsScene` with the existing 1000 x 700 working extent. Its initial rendering method clears the scene and leaves it empty; the view background becomes neutral white. No external basemap service, local raster, or simulated map content is added.

The current coordinate conversion remains as a temporary UI-status mechanism. Later file-loading work can add graphics items to the same scene and register them as selectable without changing the surrounding window layout.

## Validation

- Instantiate `MainWindow` using Qt's offscreen platform.
- Assert that the map scene contains no initial mock items and no selectable items.
- Run Ruff and Python compilation checks.
