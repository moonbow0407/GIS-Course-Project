# Four-Layer Vector GIS MVP Design

## Goal

Refactor the existing PySide6 prototype into a real four-layer GIS desktop application and deliver one complete vector workflow:

1. Open a local Shapefile or GeoJSON file.
2. Convert it into the application's own vector domain model.
3. Add it to the map document and layer panel.
4. Render point, line, polygon, and multipart geometries on the map canvas.
5. Support pan, zoom, full extent, point selection, rectangle selection, and attribute display.

The result must be a usable vector-data MVP rather than a collection of unimplemented classes.

## Course-Requirement Mapping

The course guide defines four layers: user interface, business functions, data model, and data access. This design names them as follows:

| Course layer | Project package | Responsibility |
| --- | --- | --- |
| User interface | `app/presentation` | Qt windows, widgets, dialogs, rendering, and user feedback |
| Business functions | `app/application` | Use-case orchestration, workspace commands, and query coordination |
| Data model | `app/domain` | Features, vector layers, styles, CRS, selections, and map-document rules |
| Data access | `app/infrastructure` | GeoPandas/Fiona file adapters and future raster/database adapters |

Dependencies point inward:

```text
Presentation -> Application -> Domain
                         ^
Infrastructure ----------+
```

`app/bootstrap.py` is the composition root that constructs concrete adapters and injects them into the application and presentation layers.

## Scope

### Included

- Replace the current `models`, `services`, and top-level UI layout with the four-layer package structure.
- Use Shapely geometry objects and PyProj CRS objects in the domain model.
- Read `.shp`, `.geojson`, and `.json` vector files with GeoPandas.
- Preserve feature identifiers, attributes, geometry, source path, CRS, and layer bounds.
- Reproject later known-CRS layers to the map document's established display CRS before domain conversion.
- Reject missing files, unsupported formats, unreadable files, empty datasets, and datasets with no usable geometries.
- Allow a missing CRS but show a clear warning and label the coordinate system as unknown.
- Render points, lines, polygons, multiparts, polygon holes, and geometry collections.
- Keep the existing blank-map startup requirement: no mock layers, no fake selection, and no fake coordinate or scale values.
- Provide a compact professional Ribbon-style workbench using local QSS and local SVG icons.
- Add automated tests for all four layers and keep Ruff, mypy, and pytest passing.

### Excluded

- Raster loading and rendering.
- Vector editing and export.
- Line smoothing and simplification.
- Buffer or overlay analysis.
- PostgreSQL/PostGIS connectivity.
- Basemap services and online resources.

The excluded features remain visible only when useful for course navigation and are disabled or clearly marked as unavailable. They must not pretend to execute.

## Package Structure

```text
app/
├── application/
│   ├── errors.py
│   ├── gis_application.py
│   ├── ports.py
│   └── results.py
├── domain/
│   ├── feature.py
│   ├── layer_style.py
│   ├── map_document.py
│   └── vector_layer.py
├── infrastructure/
│   └── file_io/
│       └── geopandas_vector_reader.py
├── presentation/
│   ├── main_window.py
│   ├── renderers/
│   │   └── qt_vector_renderer.py
│   ├── resources/
│   │   ├── icons/
│   │   └── styles/
│   └── widgets/
│       ├── layer_panel.py
│       ├── map_canvas.py
│       └── right_panel.py
└── bootstrap.py
```

Tests mirror these packages under `tests/`. Small generated GeoJSON fixtures live under `tests/fixtures`; tests do not depend on large external datasets.

## Domain Model

### Feature

`Feature` is a slotted dataclass containing:

- `fid: str | int`
- `geometry: BaseGeometry`
- `attributes: Mapping[str, AttributeValue]`

Attribute values are limited to JSON-like scalar values plus dates and null. Infrastructure normalizes NumPy/Pandas scalar values before constructing a feature.

### VectorLayer

`VectorLayer` contains:

- stable application-generated `layer_id`
- display `name`
- optional `CRS`
- tuple of `Feature` objects
- source path
- geometry-family summary
- bounds derived from valid, non-empty geometries
- `LayerStyle`

The layer validates that it contains at least one usable geometry. The underlying source format is not exposed to callers except as metadata.

### MapDocument

`MapDocument` is the single source of truth for ordered layers, visibility, active layer, and selection. It provides operations to add, remove, reorder, show/hide, activate, and select features. It rejects duplicate layer identifiers and clears stale selection when a layer is removed or hidden.

The presentation never owns an independent list of mock layers.

## Application Interface

`GisApplication` is the interface used by the presentation. Its first release exposes:

```python
open_vector(path: Path) -> OpenVectorResult
remove_layer(layer_id: str) -> WorkspaceSnapshot
move_layer(layer_id: str, target_index: int) -> WorkspaceSnapshot
set_layer_visibility(layer_id: str, visible: bool) -> WorkspaceSnapshot
set_active_layer(layer_id: str) -> WorkspaceSnapshot
select_point(point: Point, tolerance: float) -> SelectionResult
select_rectangle(rectangle: Polygon) -> SelectionResult
clear_selection() -> SelectionResult
snapshot() -> WorkspaceSnapshot
```

The result objects are immutable dataclasses. Qt widgets receive snapshots and results rather than mutable domain collections.

`VectorReader` is the only initial port because it has both a production GeoPandas adapter and an in-memory test adapter. Its interface is `read(path: Path, target_crs: CRS | None = None) -> VectorLayer`; the optional target CRS makes reprojection explicit. No speculative reader/analyzer base-class hierarchy is introduced.

## Vector Loading Flow

```text
User chooses a file
-> MainWindow calls GisApplication.open_vector(path)
-> GisApplication calls VectorReader.read(path, document.display_crs)
-> GeoPandasVectorReader validates and converts the dataset
-> GisApplication adds VectorLayer to MapDocument
-> GisApplication returns OpenVectorResult and WorkspaceSnapshot
-> MainWindow refreshes LayerPanel and MapCanvas
-> MapCanvas fits the first loaded layer extent
-> Status bar shows layer name, feature count, CRS, and no selection
```

The map document uses the first known layer CRS as the display CRS. A later layer with a different known CRS is reprojected by the infrastructure adapter before domain conversion. If the first layer has no CRS, the document remains in an explicitly unknown coordinate system; additional unknown-CRS layers may be loaded with a warning, but a known-CRS layer is rejected because the existing unknown data cannot be transformed safely. Once a display CRS is established, a new layer with no CRS is rejected. This prevents silently overlaying incompatible coordinates.

## Rendering and Interaction

`QtVectorRenderer` converts Shapely geometries into Qt graphics items:

- `Point` and `MultiPoint` -> ellipse items
- `LineString` and `MultiLineString` -> painter paths
- `Polygon` and `MultiPolygon` -> painter paths with odd-even fill for holes
- `GeometryCollection` -> recursive rendering of supported members

Each item stores its layer identifier and feature identifier as Qt item data. The renderer owns style conversion and selection highlighting.

`MapCanvas` owns map/view transforms and interaction state. Domain coordinates are mapped directly into scene coordinates with the vertical axis inverted in one documented transform. It emits map coordinates and selection requests; it does not perform file I/O or business queries.

Point selection delegates to Shapely distance/intersection logic through `GisApplication`. Rectangle selection uses a Shapely box and returns every intersecting feature in visible layers, prioritizing the active layer for point selection.

## User Interface

The main window keeps the course-guide layout:

- compact Ribbon tabs and grouped tools at the top
- content/layer panel on the left
- map workspace in the center
- attributes panel on the right
- coordinates, CRS, active layer, selection count, and data-source state in the status bar

On startup:

- layer panel is empty
- map canvas displays an instructional empty state
- active layer is `None`
- selection count is `0`
- coordinate, scale, and CRS labels display `--`

After loading, the empty-state instruction disappears. The layer panel is populated only from a `WorkspaceSnapshot`. Local SVG icons use one visual family, and unsupported course features are disabled with explanatory tooltips.

## Error Handling

Infrastructure exceptions are translated into application errors:

- `VectorFileNotFound`
- `UnsupportedVectorFormat`
- `VectorReadFailed`
- `EmptyVectorDataset`
- `NoUsableGeometry`
- `IncompatibleCoordinateReferenceSystem`
- `LayerNotFound`

The presentation catches only application errors and displays a concise dialog plus a status-bar message. Unexpected exceptions are logged with context and shown as a generic failure without leaking a traceback to the user.

Loading is atomic: a failed read or conversion never partially mutates `MapDocument` or the current scene.

## Testing Strategy

### Domain

- feature and vector-layer validation
- bounds and geometry-family calculation
- add/remove/reorder/visibility invariants
- active-layer and selection cleanup

### Application

- open-vector success with an in-memory reader
- reader failure leaves the document unchanged
- visibility and ordering commands
- point and rectangle selection
- structured result snapshots

### Infrastructure

- temporary GeoJSON point, line, polygon, multipart, null-geometry, and empty datasets
- CRS preservation and target-CRS conversion
- field-value normalization
- error translation

### Presentation

- empty initial state
- opening a file through an injected fake application
- snapshot-to-layer-panel synchronization
- geometry rendering and full extent
- selection-to-attribute-panel synchronization
- Ribbon tabs, grouped commands, and disabled unavailable commands

Tests may inspect public interfaces and Qt-visible state, not private widget collections.

## Migration Sequence

1. Add domain models and tests; keep the old UI running.
2. Add application interfaces, snapshots, and tests.
3. Add the GeoPandas adapter and integration tests.
4. Move and replace presentation modules behind the application interface.
5. Add rendering, queries, and attribute display.
6. Replace the mock initial state and remove obsolete unimplemented modules.
7. Apply Ribbon, SVG icons, empty state, and status-bar polish.
8. Update README and architecture documentation.

Each step must keep the repository runnable or be completed in the same change as its dependent step. Obsolete modules are removed only after all callers have migrated.

## Acceptance Criteria

1. The application starts with an empty, coherent workspace and no mock GIS data.
2. A valid Shapefile or GeoJSON can be opened from the UI and appears in both the layer panel and map canvas.
3. Point, line, polygon, multipart geometries, and polygon holes render correctly.
4. Pan, wheel zoom, toolbar zoom, full extent, point selection, rectangle selection, and attribute display work on loaded data.
5. Layer visibility, activation, deletion, and reordering update both panels and the canvas consistently.
6. CRS is retained, incompatible layers are not silently overlaid, and missing CRS is communicated clearly.
7. File failures leave the existing workspace unchanged and produce a user-facing error.
8. `uv run ruff check .`, `uv run mypy app`, and `uv run pytest` all pass.
9. The main window follows the course-guide layout and the approved professional Ribbon visual direction.
10. README documents installation, launch, supported formats, current limitations, and test commands.
