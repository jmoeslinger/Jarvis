import io
from PIL import Image, ImageDraw, ImageFont

_STATE_COLORS = {
    "idle":              (45,  45,  55),
    "wake_listening":    (20,  80, 180),
    "command_listening": (20, 160,  80),
    "processing":        (180, 120,  0),
    "speaking":          (20, 160,  80),
    "error":             (180,  40,  40),
}

_GLOW_COLORS = {
    "idle":              (70,  70,  90),
    "wake_listening":    (40, 120, 220),
    "command_listening": (40, 200, 100),
    "processing":        (220, 150,  20),
    "speaking":          (40, 200, 100),
    "error":             (220,  60,  60),
}


def create_jarvis_icon(size: int = 64, state: str = "idle") -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bg = _STATE_COLORS.get(state, _STATE_COLORS["idle"])
    glow = _GLOW_COLORS.get(state, _GLOW_COLORS["idle"])

    # Aeusserer Glow-Ring
    draw.ellipse([1, 1, size - 1, size - 1], fill=(*glow, 60), outline=glow, width=2)

    # Hauptkreis
    margin = 4
    draw.ellipse([margin, margin, size - margin, size - margin], fill=(*bg, 255))

    # Innerer Highlight-Ring
    draw.ellipse(
        [margin + 2, margin + 2, size - margin - 2, size - margin - 2],
        fill=None,
        outline=(*glow, 120),
        width=1,
    )

    # "J" Buchstabe
    center = size // 2
    font_size = int(size * 0.48)
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()

    draw.text((center, center), "J", fill=(255, 255, 255, 240), font=font, anchor="mm")

    return img


def get_icon_image(size: int = 64, state: str = "idle") -> Image.Image:
    return create_jarvis_icon(size, state)
