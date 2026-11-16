"""Pure image operations for Cropaganda: no AWS calls here, so it can be tested locally.

- load_photo(): decode, honour EXIF orientation, convert to sRGB, drop metadata
- cover_crop(): fill a target size, keeping the chosen focal point in frame
- draw_overlay(): event banner (name, date/location, hashtag, optional logo),
  or the event's own banner image (draw_custom_banner) when one was uploaded,
  or a full-frame overlay drawn over the whole photo (draw_frame)
- render(): the three steps above for one format, returns JPEG bytes
"""

from __future__ import annotations

import io
import math
import os
from dataclasses import dataclass, field

from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps

# Refuse decompression bombs, but allow large DSLR / phone photos (~ 100 MP).
Image.MAX_IMAGE_PIXELS = 120_000_000

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
FONT_BOLD = os.path.join(FONT_DIR, "Inter-Bold.otf")
FONT_SEMIBOLD = os.path.join(FONT_DIR, "Inter-SemiBold.otf")
FONT_MEDIUM = os.path.join(FONT_DIR, "Inter-Medium.otf")

_SRGB = ImageCms.createProfile("sRGB")


@dataclass
class Overlay:
    name: str
    date_label: str = ""
    location: str = ""
    hashtag: str = ""
    primary_color: str = "#111827"
    accent_color: str = "#F59E0B"
    text_color: str = "#FFFFFF"
    position: str = "bottom"  # "bottom" | "top"
    logo: Image.Image | None = field(default=None, repr=False)
    banner: Image.Image | None = field(default=None, repr=False)  # user-made strip, replaces the generated banner
    frame: Image.Image | None = field(default=None, repr=False)  # full-frame overlay, replaces both

    @classmethod
    def from_event(
        cls, event: dict, logo: Image.Image | None = None, banner: Image.Image | None = None, frame: Image.Image | None = None
    ) -> "Overlay":
        return cls(
            name=event.get("name", ""),
            date_label=event.get("dateLabel", ""),
            location=event.get("location", ""),
            hashtag=event.get("hashtag", ""),
            primary_color=event.get("primaryColor") or cls.primary_color,
            accent_color=event.get("accentColor") or cls.accent_color,
            text_color=event.get("textColor") or cls.text_color,
            position=event.get("position") or "bottom",
            logo=logo,
            banner=banner,
            frame=frame,
        )


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        raise ValueError(f"invalid colour {value!r}")
    return tuple(int(v[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def load_photo(data: bytes) -> Image.Image:
    """Decode an uploaded photo into an upright RGB image in sRGB, without metadata."""
    img = Image.open(io.BytesIO(data))
    img.draft("RGB", (8000, 8000))  # lets libjpeg skip work on huge JPEGs
    img = ImageOps.exif_transpose(img)
    icc = img.info.get("icc_profile")
    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            img = ImageCms.profileToProfile(img, src, _SRGB, outputMode="RGB")
        except (ImageCms.PyCMSError, OSError):
            pass  # unknown profile: keep pixels as they are
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.getchannel("A"))
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.info.clear()  # strip EXIF (incl. GPS) and other metadata
    return img


def load_logo(data: bytes) -> Image.Image:
    logo = Image.open(io.BytesIO(data))
    logo = ImageOps.exif_transpose(logo)
    return logo.convert("RGBA")


def cover_crop(img: Image.Image, width: int, height: int, focus_x: float = 0.5, focus_y: float = 0.5) -> Image.Image:
    """Scale and crop `img` to exactly width x height, centring the crop on the focal point."""
    src_w, src_h = img.size
    scale = max(width / src_w, height / src_h)
    # width / (width / src_w) can come out a hair above src_w in floating point, which would make
    # `src_w - crop_w` slightly negative and Pillow reject the box ("box offset can't be negative").
    crop_w = min(width / scale, src_w)
    crop_h = min(height / scale, src_h)
    fx = min(max(focus_x, 0.0), 1.0) * src_w
    fy = min(max(focus_y, 0.0), 1.0) * src_h
    left = max(min(fx - crop_w / 2, src_w - crop_w), 0.0)
    top = max(min(fy - crop_h / 2, src_h - crop_h), 0.0)
    box = (left, top, left + crop_w, top + crop_h)
    return img.resize((width, height), Image.Resampling.LANCZOS, box=box, reducing_gap=3.0)


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: float) -> str:
    """Truncate with an ellipsis so the text fits on one line."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if draw.textlength(text[:mid].rstrip() + ell, font=font) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + ell if lo else ""


def _scrim(size: tuple[int, int], band: int, position: str, strength: int = 150) -> Image.Image:
    """Transparent-to-dark gradient behind the banner so it reads on bright photos."""
    w, h = size
    grad = Image.new("L", (1, band))
    for y in range(band):
        t = y / max(band - 1, 1)
        grad.putpixel((0, y), int(strength * (t**1.6)))
    grad = grad.resize((w, band))
    if position == "top":
        grad = grad.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    black = Image.new("RGBA", (w, band), (0, 0, 0, 255))
    black.putalpha(grad)
    layer.paste(black, (0, h - band if position == "bottom" else 0))
    return layer


# Nominal width / height of each full-frame slot. A format without a frame of its own shape
# borrows the uploaded frame closest to its ratio (stretched to fit). Mirrored in preview.js.
FRAME_RATIOS = {"square": 1.0, "portrait": 4 / 5, "story": 9 / 16, "landscape": 16 / 9}


def frame_key_for(fmt: dict, frames: dict) -> str | None:
    """Pick the frame (any value type) for a format: its own slot, else the nearest ratio."""
    frames = {k: v for k, v in (frames or {}).items() if v and k in FRAME_RATIOS}
    if not frames:
        return None
    if fmt.get("frame") in frames:
        return frames[fmt["frame"]]
    ratio = fmt["width"] / fmt["height"]
    best = min(sorted(frames), key=lambda s: abs(math.log(FRAME_RATIOS[s] / ratio)))
    return frames[best]


def draw_frame(img: Image.Image, frame: Image.Image) -> Image.Image:
    """Full-frame overlay: scaled to exactly cover the output, transparency respected."""
    base = img.convert("RGBA")
    base.alpha_composite(frame.resize(base.size, Image.Resampling.LANCZOS, reducing_gap=3.0))
    return base.convert("RGB")


# A custom banner never covers more than this share of the output height.
BANNER_MAX_HEIGHT = 0.35


def banner_box(w: int, h: int, bw: int, bh: int, position: str, safe_top: float = 0.0, safe_bottom: float = 0.0) -> tuple[int, int, int, int]:
    """Where a bw x bh banner goes on a w x h output: (x, y, width, height).

    Full output width, aspect ratio kept, against the top or bottom edge but outside the platform's
    UI zones. A banner that would be taller than BANNER_MAX_HEIGHT is scaled down and centred instead.
    Mirrored by bannerBox() in frontend/js/preview.js.
    """
    top = round(h * safe_top)
    bottom = h - round(h * safe_bottom)
    max_h = min(round(h * BANNER_MAX_HEIGHT), bottom - top)
    tw, th = w, max(1, round(bh * w / bw))
    if th > max_h:
        th = max(1, max_h)
        tw = max(1, round(bw * th / bh))
    x = (w - tw) // 2
    y = top if position == "top" else bottom - th
    return x, y, tw, th


def draw_custom_banner(img: Image.Image, banner: Image.Image, position: str, safe_top: float = 0.0, safe_bottom: float = 0.0) -> Image.Image:
    """Composite the event's own banner image (transparency respected) and return a new RGB image."""
    w, h = img.size
    x, y, tw, th = banner_box(w, h, banner.width, banner.height, position, safe_top, safe_bottom)
    base = img.convert("RGBA")
    base.alpha_composite(banner.resize((tw, th), Image.Resampling.LANCZOS, reducing_gap=3.0), (x, y))
    return base.convert("RGB")


def draw_overlay(img: Image.Image, overlay: Overlay, safe_top: float = 0.0, safe_bottom: float = 0.0) -> Image.Image:
    """Draw the event banner on an RGB image and return a new RGB image."""
    if overlay.frame is not None:
        return draw_frame(img, overlay.frame)
    if overlay.banner is not None:
        return draw_custom_banner(img, overlay.banner, overlay.position, safe_top, safe_bottom)
    w, h = img.size
    # Layout unit: scales with the output's area, but tall formats are capped by their width.
    u = min(math.sqrt(w * h), w * 1.1) / 100
    margin = round(4 * u)
    pad_x, pad_y = round(2.6 * u), round(2.0 * u)
    gap = round(0.7 * u)
    stripe = max(3, round(0.7 * u))
    radius = round(1.4 * u)

    name_size = max(12, round(4.4 * u))
    f_name = ImageFont.truetype(FONT_BOLD, name_size)
    f_meta = ImageFont.truetype(FONT_MEDIUM, max(10, round(2.5 * u)))
    f_tag = ImageFont.truetype(FONT_SEMIBOLD, max(10, round(2.5 * u)))

    text_rgb = hex_to_rgb(overlay.text_color)
    accent_rgb = hex_to_rgb(overlay.accent_color)
    primary_rgb = hex_to_rgb(overlay.primary_color)

    base = img.convert("RGBA")
    base.alpha_composite(_scrim((w, h), round(h * 0.42), overlay.position))

    measure = ImageDraw.Draw(base)
    meta = "  ·  ".join(p for p in (overlay.date_label.strip(), overlay.location.strip()) if p)
    tag = overlay.hashtag.strip()
    if tag and not tag.startswith("#"):
        tag = "#" + tag

    lines: list[tuple[str, ImageFont.FreeTypeFont, tuple[int, int, int]]] = []
    if overlay.name.strip():
        lines.append((overlay.name.strip(), f_name, text_rgb))
    if meta:
        lines.append((meta, f_meta, text_rgb))
    if tag:
        lines.append((tag, f_tag, accent_rgb))
    if not lines and overlay.logo is None:
        return base.convert("RGB")

    def line_h(font: ImageFont.FreeTypeFont) -> int:
        asc, desc = font.getmetrics()
        return asc + desc

    text_h = sum(line_h(f) for _, f, _ in lines) + gap * max(len(lines) - 1, 0)
    content_h = max(text_h, round(9 * u) if overlay.logo is not None else 0)

    logo_img = None
    logo_w = 0
    if overlay.logo is not None:
        lh = content_h
        lw = round(overlay.logo.width * lh / overlay.logo.height)
        max_lw = round(w * 0.28)
        if lw > max_lw:
            lw, lh = max_lw, round(overlay.logo.height * max_lw / overlay.logo.width)
        logo_img = overlay.logo.resize((max(lw, 1), max(lh, 1)), Image.Resampling.LANCZOS)
        logo_w = logo_img.width + round(2.2 * u)

    max_panel_w = w - 2 * margin
    max_text_w = max_panel_w - stripe - 2 * pad_x - logo_w
    # Long event names: shrink the title down to 70 % before truncating it.
    if lines and lines[0][1] is f_name:
        size = name_size
        while size > name_size * 0.7 and measure.textlength(lines[0][0], font=f_name) > max_text_w:
            size -= 1
            f_name = ImageFont.truetype(FONT_BOLD, size)
        lines[0] = (lines[0][0], f_name, lines[0][2])
        text_h = sum(line_h(f) for _, f, _ in lines) + gap * max(len(lines) - 1, 0)
        content_h = max(text_h, logo_img.height if logo_img is not None else 0)
    fitted = [(_fit_text(measure, t, f, max_text_w), f, c) for t, f, c in lines]
    text_w = max((measure.textlength(t, font=f) for t, f, _ in fitted), default=0)

    panel_w = round(stripe + 2 * pad_x + logo_w + text_w)
    panel_h = round(content_h + 2 * pad_y)
    x0 = margin
    if overlay.position == "top":
        y0 = round(h * safe_top) + margin
    else:
        y0 = h - round(h * safe_bottom) - margin - panel_h

    panel = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(panel)
    d.rounded_rectangle((x0, y0, x0 + panel_w, y0 + panel_h), radius=radius, fill=primary_rgb + (235,))
    d.rounded_rectangle((x0, y0, x0 + stripe + radius, y0 + panel_h), radius=radius, fill=accent_rgb + (255,))
    d.rectangle((x0 + stripe, y0, x0 + stripe + radius, y0 + panel_h), fill=primary_rgb + (235,))
    base.alpha_composite(panel)

    cx = x0 + stripe + pad_x
    if logo_img is not None:
        base.alpha_composite(logo_img, (cx, y0 + pad_y + (content_h - logo_img.height) // 2))
        cx += logo_w

    draw = ImageDraw.Draw(base)
    ty = y0 + pad_y + (content_h - text_h) // 2
    for text, font, colour in fitted:
        draw.text((cx, ty), text, font=font, fill=colour + (255,))
        ty += line_h(font) + gap

    return base.convert("RGB")


def render(photo: Image.Image, fmt: dict, overlay: Overlay | None, focus_x: float = 0.5, focus_y: float = 0.5, quality: int = 88) -> bytes:
    out = cover_crop(photo, fmt["width"], fmt["height"], focus_x, focus_y)
    if overlay is not None:
        out = draw_overlay(out, overlay, fmt.get("safeTop", 0), fmt.get("safeBottom", 0))
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()
