# ArcGIS Pro 风格 GIS 工作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 PySide6 GIS 主界面重构为浅色 ArcGIS Pro 风格工作台，同时保留已有地图和图层交互。

**Architecture:** MainWindow 负责将现有 QAction 映射到“功能页签 + Ribbon 工具组”，不改变动作的业务槽函数。LayerPanel、RightPanel 和 MapCanvas 仅增加可识别的控件对象名和工作区容器，视觉表现集中在全局 QSS；这样可用现有信号边界保护 GIS 交互逻辑。

**Tech Stack:** Python 3.10、PySide6 6.7+、pytest、ruff、mypy、QSS。

## Global Constraints

- 不改变文件读写、地图选择、图层排序、缓冲区参数校验的业务逻辑。
- 不使用网络资源、品牌素材或未提交图片素材。
- 保持 Python 3.10 兼容，代码标识符使用英文。
- 默认显示“地图”功能页签，中央地图工作区优先伸缩。
- 只操作本次改造涉及的文件；保留工作区已有的未提交改动。

---

## File Structure

- Modify: `app/main_window.py` — 创建快速访问栏、功能页签、Ribbon 工具组和地图工作区容器。
- Modify: `app/widgets/layer_panel.py` — 提供“内容”面板搜索和工具区的对象名与过滤交互。
- Modify: `app/widgets/right_panel.py` — 统一属性/分析页签的对象名和表单视觉结构。
- Modify: `app/widgets/map_canvas.py` — 为地图工作区提供浅灰画布和白色场景边界。
- Modify: `app/resources/styles/main.qss` — 定义浅色 GIS 工作台视觉令牌和控件状态。
- Create: `tests/test_main_window.py` — 验证 Ribbon 默认页签、地图工作区和状态栏。
- Modify: `tests/test_map_canvas.py` — 验证空画布场景边界不影响空数据状态。

## Task 1: 建立主窗口工作台骨架

**Files:**
- Create: `tests/test_main_window.py`
- Modify: `app/main_window.py`

**Interfaces:**
- Consumes: `LayerPanel`, `MapCanvas`, `RightPanel` 的现有构造函数与信号。
- Produces: `MainWindow.ribbon_tabs: QTabWidget`、`MainWindow.map_tabs: QTabWidget` 和 `MainWindow.quick_access_toolbar: QToolBar`。

- [ ] **Step 1: Write the failing test**

```python
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.main_window import MainWindow


def test_main_window_defaults_to_map_ribbon_and_map_tab() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    assert app is not None
    assert window.ribbon_tabs.tabText(window.ribbon_tabs.currentIndex()) == "地图"
    assert window.map_tabs.tabText(0) == "地图"
    assert window.quick_access_toolbar.objectName() == "quickAccessToolBar"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_main_window.py::test_main_window_defaults_to_map_ribbon_and_map_tab -v`

Expected: FAIL because `MainWindow` has no `ribbon_tabs` or `map_tabs` attributes.

- [ ] **Step 3: Write minimal implementation**

In `app/main_window.py`, add a `QTabWidget` named `ribbonTabs` below a compact `quickAccessToolBar`; build one page per 功能页签 with grouped, non-movable `QToolBar` instances that reuse `self._actions`. Replace the direct central map insertion with a `map_tabs` `QTabWidget` containing `self.map_canvas`, then put it between the two existing side panels in the splitter. Set the ribbon’s current page to “地图”.

```python
self.ribbon_tabs = QTabWidget()
self.ribbon_tabs.setObjectName("ribbonTabs")
self.map_tabs = QTabWidget()
self.map_tabs.setObjectName("mapTabs")
self.map_tabs.addTab(self.map_canvas, "地图")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_main_window.py::test_main_window_defaults_to_map_ribbon_and_map_tab -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main_window.py tests/test_main_window.py
git commit -m "feat: add GIS workbench ribbon shell"
```

## Task 2: 完善内容和属性停靠面板

**Files:**
- Modify: `app/widgets/layer_panel.py`
- Modify: `app/widgets/right_panel.py`
- Modify: `tests/test_main_window.py`

**Interfaces:**
- Consumes: 现有 `LayerPanel.layer_names()` 和 `RightPanel.set_layer_names()`。
- Produces: LayerPanel 的 `layerSearchInput` 和 RightPanel 的 `rightPanelTabs` 对象名，供 QSS 和测试定位。

- [ ] **Step 1: Write the failing test**

```python
def test_workbench_uses_content_and_inspector_panels() -> None:
    window = MainWindow()

    assert window.layer_panel.findChild(QLineEdit, "layerSearchInput") is not None
    assert window.right_panel.findChild(QTabWidget, "rightPanelTabs") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_main_window.py::test_workbench_uses_content_and_inspector_panels -v`

Expected: FAIL because the controls have no requested object names.

- [ ] **Step 3: Write minimal implementation**

Add a `QLineEdit` with placeholder `搜索图层` above the layer tree. On `textChanged`, hide top-level items whose text does not contain the search text (case-insensitive); empty text shows all items. Set its object name to `layerSearchInput`. Set the existing RightPanel tab widget object name to `rightPanelTabs`, and change tab captions to `属性` and `分析`.

```python
def _filter_layers(self, text: str) -> None:
    keyword = text.casefold().strip()
    for row in range(self._tree.topLevelItemCount()):
        item = self._tree.topLevelItem(row)
        item.setHidden(bool(keyword) and keyword not in item.text(0).casefold())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_main_window.py::test_workbench_uses_content_and_inspector_panels -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/widgets/layer_panel.py app/widgets/right_panel.py tests/test_main_window.py
git commit -m "feat: refine GIS content and inspector panels"
```

## Task 3: 定义地图工作区和浅色专业样式

**Files:**
- Modify: `app/widgets/map_canvas.py`
- Modify: `app/resources/styles/main.qss`
- Modify: `tests/test_map_canvas.py`

**Interfaces:**
- Consumes: `MapCanvas.scene()` 及现有缩放/选择逻辑。
- Produces: 空数据状态下的白色地图场景边界和 `mapCanvas` 对象名。

- [ ] **Step 1: Write the failing test**

```python
def test_canvas_identifies_the_map_workspace_without_data_items() -> None:
    canvas = MapCanvas()

    assert canvas.objectName() == "mapCanvas"
    assert canvas.scene().items() == []
    assert canvas.scene().sceneRect().width() == 1000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_map_canvas.py::test_canvas_identifies_the_map_workspace_without_data_items -v`

Expected: FAIL because the map canvas has no object name.

- [ ] **Step 3: Write minimal implementation**

Set `MapCanvas` object name to `mapCanvas`; retain the existing empty scene and scene rectangle. Replace `main.qss` with a complete style for `quickAccessToolBar`, `ribbonTabs`, `ribbonGroup`, `mapTabs`, content tree, property tabs, form controls and status bar. Use blue `#2f7de1`, white panels, `#f5f6f8` workbench background, and `#d9dde5` one-pixel borders; avoid card shadows and large rounded corners.

```python
self.setObjectName("mapCanvas")
self.setBackgroundBrush(QBrush(QColor("#f5f6f8")))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_map_canvas.py::test_canvas_identifies_the_map_workspace_without_data_items -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/widgets/map_canvas.py app/resources/styles/main.qss tests/test_map_canvas.py
git commit -m "style: apply ArcGIS Pro inspired workbench theme"
```

## Task 4: 回归验证与可视检查

**Files:**
- Modify: `tests/test_main_window.py` only if a discovered layout invariant requires coverage.

**Interfaces:**
- Consumes: Tasks 1–3 complete UI hierarchy.
- Produces: verified workbench layout and no regressions in current map-canvas behavior.

- [ ] **Step 1: Run targeted UI tests**

Run: `uv run pytest tests/test_main_window.py tests/test_map_canvas.py -v`

Expected: PASS for all tests.

- [ ] **Step 2: Run static checks**

Run: `uv run ruff check .`

Expected: `All checks passed!`

Run: `uv run mypy app`

Expected: `Success: no issues found`.

- [ ] **Step 3: Perform a manual visual launch**

Run: `uv run python main.py`

Expected: application starts with the Map ribbon selected; the central area is visibly wider than either dock panel; toolbar actions still trigger the existing handlers.

- [ ] **Step 4: Commit verification adjustments if any**

```bash
git add app tests
git commit -m "test: verify GIS workbench layout"
```

## Self-Review

- Spec coverage: Tasks 1–3 respectively cover Ribbon/workspace, left and right panels, map visual treatment and status-bar-compatible QSS. Task 4 verifies all existing interactions remain available.
- Placeholder scan: no TBD/TODO or unspecified commands remain.
- Type consistency: `ribbon_tabs`, `map_tabs`, `quick_access_toolbar`, `layerSearchInput`, `rightPanelTabs`, and `mapCanvas` use the same names in producing and consuming tasks.
