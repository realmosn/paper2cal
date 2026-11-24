#!/usr/bin/env python3
"""Send one appointment photo to Claude and print the extracted events."""

import base64
import datetime as dt
import io
import json
import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()
load_dotenv()

ROOT = Path(__file__).parent
OUT = ROOT / "out"
MODEL = os.environ.get("MODEL", "claude-sonnet-5-5")


def load_image(path: Path) -> str:
    """Prepare the photo exactly like the Shortcut will: upright, max 1568 px, JPEG, base64."""
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    img.thumbnail((1568, 1568))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def today() -> dt.date:
    """Today's date, or PAPER2CAL_TODAY=YYYY-MM-DD to pin it (makes test runs reproducible)."""
    pinned = os.environ.get("PAPER2CAL_TODAY")
    return dt.date.fromisoformat(pinned) if pinned else dt.date.today()


def system_prompt() -> str:
    date = today().strftime("%Y-%m-%d (%A)")
    return (ROOT / "prompts/system.md").read_text().replace("{today}", date)


def extract(path: Path) -> tuple[str, anthropic.types.Message]:
    """Run one photo through the model. Saves the raw response, returns (answer text, response)."""
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=system_prompt(),
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": load_image(path),
                        },
                    },
                    {"type": "text", "text": (ROOT / "prompts/user.md").read_text()},
                ],
            }
        ],
    )
    OUT.mkdir(exist_ok=True)
    (OUT / f"{path.stem}.raw.json").write_text(resp.model_dump_json(indent=2))

    # The response can contain several blocks (e.g. "thinking" before the answer).
    # Only the "text" blocks hold the JSON.
    text = "".join(b.text for b in resp.content if b.type == "text")
    return text, resp


def main(path: Path) -> None:
    text, resp = extract(path)
    thinking = sum(1 for b in resp.content if b.type == "thinking")
    print(
        f"model={resp.model}  tokens in/out={resp.usage.input_tokens}/{resp.usage.output_tokens}"
        f"  stop={resp.stop_reason}  thinking blocks={thinking}"
    )
    try:
        print(json.dumps(json.loads(text), indent=2, ensure_ascii=False))
    except json.JSONDecodeError:
        print("!! Response is not valid JSON:\n" + text)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python extract.py samples/<photo>")
    main(Path(sys.argv[1]))
