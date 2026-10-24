"""Tests for the flow previews."""

from datetime import timedelta
from http import HTTPStatus
from io import BytesIO
from unittest.mock import patch

from PIL import Image, ImageChops
import pytest
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from tarsius import Roi, outline

from custom_components.local_vision.const import DOMAIN
from custom_components.local_vision.preview import MAX_PREVIEWS, _label
from homeassistant.components.http.auth import async_sign_path
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant

from . import REGION, USER_INPUT, render_frame


async def _preview_url(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "state"}
    )
    await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], REGION)
    return result["description_placeholders"]["preview"]


@pytest.mark.usefixtures("camera")
async def test_preview_served(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """The signed preview URL from the flow serves a JPEG."""
    url = await _preview_url(hass)
    client = await hass_client_no_auth()

    response = await client.get(url)

    assert response.status == HTTPStatus.OK
    assert response.content_type == "image/jpeg"
    assert (await response.read())[:2] == b"\xff\xd8"


@pytest.mark.usefixtures("camera")
async def test_preview_unknown_or_expired(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """Unknown tokens and expired previews are not found."""
    url = await _preview_url(hass)
    client = await hass_client_no_auth()
    unknown = async_sign_path(
        hass, f"/api/{DOMAIN}/preview/{'0' * 32}", timedelta(minutes=1)
    )
    assert (await client.get(unknown)).status == HTTPStatus.NOT_FOUND

    with patch(
        "custom_components.local_vision.preview.monotonic",
        return_value=1e12,
    ):
        assert (await client.get(url)).status == HTTPStatus.NOT_FOUND


@pytest.mark.usefixtures("camera")
async def test_preview_count_is_bounded(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    """Old previews make room once too many are open."""
    first = await _preview_url(hass)
    for _ in range(MAX_PREVIEWS):
        last = await _preview_url(hass)
    client = await hass_client_no_auth()

    assert (await client.get(first)).status == HTTPStatus.NOT_FOUND
    assert (await client.get(last)).status == HTTPStatus.OK


@pytest.mark.usefixtures("camera")
async def test_preview_needs_the_signature(
    hass: HomeAssistant, hass_client_no_auth: ClientSessionGenerator
) -> None:
    """Without the signed query a preview is refused."""
    url = await _preview_url(hass)
    client = await hass_client_no_auth()

    unsigned = url.split("?", 1)[0]
    assert (await client.get(unsigned)).status == HTTPStatus.UNAUTHORIZED


@pytest.mark.parametrize(
    ("box", "area"),
    [
        pytest.param((0.5, 0.5, 0.9, 0.9), (0.5, 0.25, 0.75, 0.5), id="above"),
        pytest.param((0.5, 0.0, 0.9, 0.5), (0.5, 0.0, 0.75, 0.25), id="inside"),
    ],
)
def test_labels_are_written_at_their_regions(
    box: tuple[float, float, float, float], area: tuple[float, float, float, float]
) -> None:
    """A label goes above its region, or inside at the top edge; the rest stays."""
    plain = outline(render_frame(car=False), [], max_side=640)

    labelled = _label(plain, [("A", Roi(*box))])

    with Image.open(BytesIO(plain)) as before, Image.open(BytesIO(labelled)) as after:
        assert after.size == before.size
        diff = ImageChops.difference(before.convert("L"), after.convert("L"))
    width, height = diff.size
    left, top, right, bottom = area
    label = diff.crop((left * width, top * height, right * width, bottom * height))
    elsewhere = diff.crop((0, height * 3 // 4, width // 4, height))
    # White text with a dark edge stands out, recompression barely shows.
    assert label.getextrema()[1] > 100
    assert elsewhere.getextrema()[1] < 30


def test_label_at_the_right_edge_stays_in_the_image() -> None:
    """A region at the right edge gets its label moved left, not cut off."""
    plain = outline(render_frame(car=False), [], max_side=640)

    labelled = _label(plain, [("W", Roi(0.99, 0.5, 1.0, 0.9))])

    with Image.open(BytesIO(plain)) as before, Image.open(BytesIO(labelled)) as after:
        diff = ImageChops.difference(before.convert("L"), after.convert("L"))
    width, height = diff.size
    # Left of the region, where an unmoved label would not reach.
    shifted = diff.crop((width - 20, 0, round(width * 0.99) - 1, height))
    assert shifted.getextrema()[1] > 100
