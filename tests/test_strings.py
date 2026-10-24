"""Tests for the texts of Local vision."""

import json
from pathlib import Path

from custom_components.local_vision.config_flow import DIAL_BOX_MANUAL, DIAL_BOXES
from custom_components.local_vision.const import MAX_DIALS

STRINGS = (
    Path(__file__).parents[1] / "custom_components" / "local_vision" / "strings.json"
)


def test_dial_order_texts_cover_every_dial() -> None:
    """Each dial a layout can have gets a label, each box letter a name."""
    strings = json.loads(STRINGS.read_text())

    labels = strings["config"]["step"]["dial_order"]["data"]
    assert set(labels) == {f"dial_{n}" for n in range(1, MAX_DIALS + 1)}
    assert all(
        f"{{factor_{n}}}" in labels[f"dial_{n}"] for n in range(1, MAX_DIALS + 1)
    )
    options = strings["selector"]["dial_box"]["options"]
    assert set(options) == {*DIAL_BOXES, DIAL_BOX_MANUAL}
    assert len(DIAL_BOXES) == MAX_DIALS
