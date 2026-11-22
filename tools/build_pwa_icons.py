"""Regenerate maskable PWA icons from the existing application logo."""
from pathlib import Path

from PIL import Image, ImageOps


def main() -> None:
    assets = Path(__file__).resolve().parent.parent / "football_analysis" / "assets"
    logo = Image.open(assets / "football_logo.jpeg").convert("RGB")
    output = assets / "pwa"
    output.mkdir(exist_ok=True)
    for size in (192, 512):
        icon = Image.new("RGB", (size, size), "white")
        mark = ImageOps.contain(logo, (int(size * .68), int(size * .68)), Image.Resampling.LANCZOS)
        icon.paste(mark, ((size - mark.width) // 2, (size - mark.height) // 2))
        icon.save(output / f"icon-{size}.png")
    print("PWA icons: 192x192, 512x512")


if __name__ == "__main__":
    main()
