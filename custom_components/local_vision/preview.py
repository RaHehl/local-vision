"""Short-lived frame previews for the flows."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from functools import partial
from io import BytesIO
import secrets
from time import monotonic

from aiohttp import web
from PIL import Image, ImageDraw, ImageFont
from tarsius import FRAME_SIDE, Roi, outline

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN

PREVIEW_URL = f"/api/{DOMAIN}/preview/{{token}}"
PREVIEW_LIFETIME = timedelta(minutes=10)
# Each is a JPEG of up to 1024 px; a flow shows one or two at a time.
MAX_PREVIEWS = 20
DATA_PREVIEWS: HassKey[Previews] = HassKey(f"{DOMAIN}_previews")


@dataclass
class Previews:
    """Rendered previews by token."""

    images: dict[str, tuple[float, bytes]] = field(default_factory=dict)

    def purge(self) -> None:
        """Drop expired previews."""
        now = monotonic()
        for token, (created, _) in list(self.images.items()):
            if now - created > PREVIEW_LIFETIME.total_seconds():
                del self.images[token]


async def async_preview_url(
    hass: HomeAssistant,
    frame: bytes,
    rois: Roi | Sequence[Roi],
    *,
    max_side: int = FRAME_SIDE,
    highlight: Roi | None = None,
    labels: Sequence[tuple[str, Roi]] = (),
) -> str:
    """Render the frame with the regions outlined; return a signed URL to it.

    The highlight region, e.g. the one being edited, gets its own colour.
    Each label is written into the top left corner of its region.
    """
    image = await hass.async_add_executor_job(
        partial(_render, frame, rois, max_side, highlight, labels)
    )
    if (previews := hass.data.get(DATA_PREVIEWS)) is None:
        # The first config flow runs before the integration is set up.
        previews = hass.data[DATA_PREVIEWS] = Previews()
        hass.http.register_view(PreviewView)
    previews.purge()
    while len(previews.images) >= MAX_PREVIEWS:
        del previews.images[next(iter(previews.images))]
    token = secrets.token_hex(16)
    previews.images[token] = (monotonic(), image)
    return async_sign_path(hass, PREVIEW_URL.format(token=token), PREVIEW_LIFETIME)


def _render(
    frame: bytes,
    rois: Roi | Sequence[Roi],
    max_side: int,
    highlight: Roi | None,
    labels: Sequence[tuple[str, Roi]],
) -> bytes:
    """Return the outlined frame as JPEG, with the labels; blocking."""
    image = outline(frame, rois, max_side=max_side, highlight=highlight)
    return _label(image, labels) if labels else image


def _label(jpeg: bytes, labels: Sequence[tuple[str, Roi]]) -> bytes:
    """Write each label above its region, or inside when there is no room.

    White with a dark edge, readable on any frame.
    """
    with Image.open(BytesIO(jpeg)) as opened:
        image = opened.convert("RGB")
    draw = ImageDraw.Draw(image)
    size = max(16, round(max(image.size) / 24))
    font = ImageFont.load_default(size=size)
    edge = max(2, size // 8)
    for text, roi in labels:
        left, top, _, _ = roi.box(image.size)
        above = top - edge - size - edge >= 0
        x = left if above else left + 2 * edge
        # A region at an edge keeps its label and outline inside the image.
        width = draw.textlength(text, font=font)
        x = max(edge, min(x, image.width - width - edge))
        draw.text(
            (x, top - edge) if above else (x, top + edge),
            text,
            fill="white",
            font=font,
            anchor="ls" if above else "la",
            stroke_width=edge,
            stroke_fill="black",
        )
    buffer = BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return buffer.getvalue()


class PreviewView(HomeAssistantView):
    """Serve previews to the flow dialogs."""

    url = PREVIEW_URL
    name = f"api:{DOMAIN}:preview"

    async def get(self, request: web.Request, token: str) -> web.Response:
        """Return a preview image."""
        previews = request.app[KEY_HASS].data[DATA_PREVIEWS]
        previews.purge()
        if (item := previews.images.get(token)) is None:
            return web.Response(status=404)
        return web.Response(body=item[1], content_type="image/jpeg")
