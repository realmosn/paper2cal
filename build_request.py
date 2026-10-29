#!/usr/bin/env python3
"""Build the request body the iPhone Shortcut sends, from the same prompt files the tests use.

python build_request.py                                # writes out/shortcut_body.txt
python build_request.py --test samples/IMG_3128.HEIC   # sends it once, exactly like the phone will
"""

import argparse
import datetime as dt
import json
import os
from pathlib import Path

import httpx

from extract import MODEL, OUT, ROOT, load_image

# Placeholders you replace with Shortcut variables
TODAY = "@@TODAY@@"
IMAGE = "@@IMAGE@@"


def template() -> str:
    body = {
        "model": MODEL,
        "max_tokens": 4096,
        "system": (ROOT / "prompts/system.md").read_text().replace("{today}", TODAY),
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": IMAGE,
                        },
                    },
                    {
                        "type": "text",
                        "text": (ROOT / "prompts/user.md").read_text().strip(),
                    },
                ],
            }
        ],
    }
    # Pure ASCII (umlauts become \u00fc etc.), so no text encoding can break it on the phone
    return json.dumps(body, ensure_ascii=True)


def test(body_template: str, photo: Path) -> None:
    """Fill the placeholders and send the request with plain HTTP, parsing the answer like the Shortcut will."""
    body = body_template.replace(
        TODAY, dt.date.today().strftime("%Y-%m-%d (%A)")
    ).replace(IMAGE, load_image(photo))
    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        content=body.encode(),
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        timeout=120,
    ).json()

    if resp.get("type") == "error":  # the Shortcut checks this too
        raise SystemExit(f"API error: {resp['error']['message']}")
    text = next(
        b["text"] for b in resp["content"] if b["type"] == "text"
    )  # skip thinking blocks
    print(json.dumps(json.loads(text), indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--test",
        type=Path,
        metavar="PHOTO",
        help="send the template once with this photo",
    )
    args = parser.parse_args()

    body_template = template()
    OUT.mkdir(exist_ok=True)
    target = OUT / "shortcut_body.txt"
    target.write_text(body_template)
    print(
        f"wrote {target.relative_to(ROOT)} ({len(body_template)} chars, model {MODEL})"
    )

    if args.test:
        test(body_template, args.test)


if __name__ == "__main__":
    main()
