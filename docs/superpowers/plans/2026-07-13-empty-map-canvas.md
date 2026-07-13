# Empty Map Canvas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start the GIS desktop platform with a blank map workspace instead of a simulated basemap.

**Architecture:** `MapCanvas` retains its `QGraphicsScene` and interaction handlers, but its initial render method only clears the scene and resets selection state. The rest of the window remains unchanged, leaving a stable target for real data-loading work.

**Tech Stack:** Python 3.10, PySide6, pytest, Ruff, uv.

## Global Constraints

- Use Python `>=3.10,<3.13` and the project-level uv virtual environment.
- Do not add online or offline basemap dependencies.
- Preserve pan, zoom, coordinate reporting, and selection event interfaces.
- Initial canvas content and selectable-feature count must both be zero.

---

### Task 1: Replace mock initial rendering with an empty workspace

**Files:**
- Modify: `D:\workspace\gis_lab\gis_desktop\app\widgets\map_canvas.py`
- Create: `D:\workspace\gis_lab\gis_desktop\tests\test_map_canvas.py`

**Interfaces:**
- Consumes: `MapCanvas()` constructor and its existing `QGraphicsScene`.
- Produces: an initial `MapCanvas` whose `scene().items()` is empty and whose private selectable collection is empty.

- [ ] **Step 1: Write the failing test**

```python
from PySide6.QtWidgets import QApplication

from app.widgets.map_canvas import MapCanvas


def test_canvas_starts_without_mock_map_items(qapp: QApplication) -> None:
    canvas = MapCanvas()

    assert canvas.scene().items() == []
    assert canvas._selectable_items == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_map_canvas.py::test_canvas_starts_without_mock_map_items -v`

Expected: FAIL because the constructor currently creates simulated map items.

- [ ] **Step 3: Write the minimal implementation**

Replace the constructor's `_draw_mock_map()` call with `_initialize_empty_workspace()`, and implement:

```python
def _initialize_empty_workspace(self) -> None:
    self._scene.clear()
    self._selectable_items.clear()
    self._selected_items.clear()
    self.setBackgroundBrush(QBrush(QColor("#ffffff")))
    self.zoom_to_full_extent()
```

Remove the unused mock-render helper methods and their imports after confirming they are no longer referenced.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_map_canvas.py::test_canvas_starts_without_mock_map_items -v`

Expected: PASS.

- [ ] **Step 5: Run project validation**

Run: `uv run python -m compileall main.py app`

Expected: compilation completes without errors.

Run: `uv run ruff check .`

Expected: `All checks passed!`.

- [ ] **Step 6: Commit**

```bash
git add app/widgets/map_canvas.py tests/test_map_canvas.py
git commit -m "feat: start with an empty GIS canvas"
```

Only perform this step after a valid Git repository is available for `D:\workspace\gis_lab\gis_desktop`.
