"""A rendered meter face, so the real tarsius reader can be run in tests."""

from io import BytesIO
import math

from PIL import Image, ImageDraw, ImageFont

SIZE = (640, 480)
WINDOW = (150, 60, 470, 140)
DIALS = ((210, 300, 60), (430, 300, 60))


def _percent(box: tuple[float, float, float, float]) -> dict[str, float]:
    width, height = SIZE
    left, top, right, bottom = box
    return {
        "left": left / width * 100,
        "top": top / height * 100,
        "right": right / width * 100,
        "bottom": bottom / height * 100,
    }


# The regions of the face, in percent like the flow forms.
DIGITS_REGION = _percent(WINDOW)
DIAL_REGIONS = [_percent((x - r, y - r, x + r, y + r)) for x, y, r in DIALS]


def _wheels(digits: str, size: tuple[int, int], roll: float) -> Image.Image:
    """Return white digits on black wheels; ``roll`` turns the last one."""
    width, height = size
    strip = Image.new("RGB", size, (25, 25, 25))
    pitch = width / len(digits)
    font = ImageFont.load_default(size=int(height * 0.8))
    for i, char in enumerate(digits):
        cell = Image.new("RGB", (int(pitch), height * 3), (25, 25, 25))
        draw = ImageDraw.Draw(cell)
        for row, value in enumerate((int(char), (int(char) + 1) % 10)):
            draw.text(
                (pitch / 2, height * (1.5 + row)),
                str(value),
                fill=(235, 235, 235),
                font=font,
                anchor="mm",
            )
        top = int(height + (roll if i == len(digits) - 1 else 0.0) * height)
        strip.paste(cell.crop((0, top, int(pitch), top + height)), (int(i * pitch), 0))
    return strip


def _dial(position: float, radius: int) -> Image.Image:
    """Return a white dial with a red pointer at ``position`` (0 to 10)."""
    side = radius * 2
    face = Image.new("RGB", (side, side), (240, 240, 235))
    draw = ImageDraw.Draw(face)
    draw.ellipse((2, 2, side - 3, side - 3), outline=(30, 30, 30), width=3)
    font = ImageFont.load_default(size=int(radius * 0.28))
    for k in range(10):
        a = math.radians(k * 36)
        x = radius + math.sin(a) * radius * 0.75
        y = radius - math.cos(a) * radius * 0.75
        draw.text((x, y), str(k), fill=(20, 20, 20), font=font, anchor="mm")
    a = math.radians(position * 36)
    tip = (radius + math.sin(a) * radius * 0.62, radius - math.cos(a) * radius * 0.62)
    left = (radius + math.cos(a) * radius * 0.16, radius + math.sin(a) * radius * 0.16)
    right = (radius - math.cos(a) * radius * 0.16, radius - math.sin(a) * radius * 0.16)
    draw.polygon((tip, left, right), fill=(200, 40, 40))
    hub = radius * 0.2
    draw.ellipse(
        (radius - hub, radius - hub, radius + hub, radius + hub), fill=(200, 40, 40)
    )
    return face


def meter_jpeg(digits: str, dials: tuple[float, ...], roll: float) -> bytes:
    """Return a meter face as JPEG, as a camera would send it."""
    frame = Image.new("RGB", SIZE, (205, 205, 200))
    draw = ImageDraw.Draw(frame)
    draw.ellipse((40, -60, 600, 500), fill=(235, 235, 230), outline=(120, 120, 120))
    left, top, right, bottom = WINDOW
    draw.rectangle((left - 8, top - 8, right + 8, bottom + 8), fill=(60, 60, 60))
    frame.paste(_wheels(digits, (right - left, bottom - top), roll), (left, top))
    for (x, y, r), position in zip(DIALS, dials, strict=True):
        frame.paste(_dial(position, r), (x - r, y - r))
    buffer = BytesIO()
    frame.save(buffer, "JPEG", quality=92)
    return buffer.getvalue()
