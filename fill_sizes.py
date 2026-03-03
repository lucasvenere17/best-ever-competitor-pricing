"""Fill in missing product sizes by reading them from product thumbnail images using Claude Vision."""

import base64
import os
import re
import time

import anthropic
from dotenv import load_dotenv

from database import get_connection, init_db

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Match sizes like "250 ml", "13.5 fl oz", "300ml", "1L"
SIZE_RE = re.compile(r"(\d+\.?\d*)\s*(ml|mL|ML|l|L|fl\.?\s*oz|oz)\b", re.IGNORECASE)


def fetch_image_base64(url: str) -> str | None:
    """Download an image and return its base64 encoding."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
            return base64.standard_b64encode(data).decode("utf-8")
    except Exception as e:
        print(f"  Failed to fetch image: {e}")
        return None


def extract_size_from_image(image_b64: str, product_name: str) -> str | None:
    """Use Claude Vision to read the size from a product image."""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=50,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"This is a product image for: {product_name}\n"
                            "What is the volume/size shown on the bottle or packaging? "
                            "Reply with ONLY the size (e.g. '250 ml' or '13.5 fl oz'). "
                            "If you cannot determine the size, reply with just 'unknown'."
                        ),
                    },
                ],
            }
        ],
    )
    answer = response.content[0].text.strip()
    if "unknown" in answer.lower():
        return None
    # Validate that the answer contains a real size
    m = SIZE_RE.search(answer)
    return m.group(0).strip() if m else None


def main():
    init_db()
    conn = get_connection()
    cur = conn.cursor()

    # Get products missing size but having an image
    cur.execute("""
        SELECT id, brand, product_name, image_url, size
        FROM products
        WHERE image_url IS NOT NULL
        AND (size IS NULL OR size LIKE '%sizes%' OR size LIKE '%colours%' OR size LIKE '%colors%')
    """)
    products = [dict(r) for r in cur.fetchall()]
    conn.close()

    print(f"Found {len(products)} products needing size data")
    if not products:
        print("Nothing to do!")
        return

    updated = 0
    failed = 0

    for i, prod in enumerate(products):
        print(f"[{i+1}/{len(products)}] {prod['brand']} - {prod['product_name'][:50]}...", end=" ")

        # Use 400px version of the image (120px thumbnails are too small to read)
        img_url = prod["image_url"].replace("_120.", "_400.")
        img_b64 = fetch_image_base64(img_url)
        if not img_b64:
            failed += 1
            continue

        size = extract_size_from_image(img_b64, prod["product_name"])
        if size:
            print(f"=> {size}")
            conn = get_connection()
            conn.execute("UPDATE products SET size = ? WHERE id = ?", (size, prod["id"]))
            conn.commit()
            conn.close()
            updated += 1
        else:
            print("=> unknown")
            failed += 1

        # Small delay to avoid rate limiting
        if (i + 1) % 50 == 0:
            time.sleep(1)

    print(f"\nDone! Updated {updated} products, {failed} could not be determined.")


if __name__ == "__main__":
    main()
