"""动态标注领域配置测试。"""

from shapely.geometry import Point

from app.domain.feature import Feature
from app.domain.labeling import (
    LabelClass,
    LabelingConfig,
    LabelPlacement,
    default_labeling_for_features,
    labeling_from_dict,
    labeling_to_dict,
)


def test_default_labeling_prefers_common_name_field() -> None:
    """首次开启标注时应优先使用名称字段，而不是依赖字段排列顺序。"""
    features = (
        Feature(
            fid=1,
            geometry=Point(0, 0),
            attributes={"code": "A01", "名称": "合肥"},
        ),
    )

    config = default_labeling_for_features(features)

    assert config.enabled is True
    assert config.classes[0].field_name == "名称"


def test_label_class_filter_can_select_one_category() -> None:
    """标注分类过滤字段和值时，只应返回匹配要素的文本。"""
    label_class = LabelClass(
        name="省会",
        field_name="name",
        filter_field="kind",
        filter_value="capital",
        placement=LabelPlacement.ABOVE,
    )
    capital = Feature(
        fid=1,
        geometry=Point(0, 0),
        attributes={"name": "合肥", "kind": "capital"},
    )
    county = Feature(
        fid=2,
        geometry=Point(1, 1),
        attributes={"name": "县城", "kind": "county"},
    )

    assert label_class.text_for(capital) == "合肥"
    assert label_class.text_for(county) is None


def test_labeling_config_round_trip_is_json_ready() -> None:
    """标注类的枚举和样式字段应能稳定转换并恢复。"""
    config = LabelingConfig(
        enabled=True,
        classes=(
            LabelClass(
                name="省名",
                field_name="province",
                placement=LabelPlacement.CENTER,
                font_size=18.0,
                offset_x=2.0,
                offset_y=-3.0,
                halo_enabled=True,
            ),
        ),
    )

    payload = labeling_to_dict(config)
    restored = labeling_from_dict(payload)

    assert restored == config
    assert payload is not None
    assert payload["classes"][0]["placement"] == "center"  # type: ignore[index]
    assert payload["classes"][0]["halo_enabled"] is True  # type: ignore[index]
