"""Image size bruteforcer for common PNG sizes."""

import zlib
import shutil
import sqlite3
from pathlib import Path
from .utils import update_data

# Full list of expected sizes
EXPECTED_SIZES = [
    # -------------------------
    # ICONS (Square)
    # -------------------------
    (16, 16), (20, 20), (24, 24), (28, 28),
    (32, 32), (48, 48), (64, 64), (72, 72),
    (96, 96), (128, 128), (192, 192), (256, 256),
    (384, 384), (512, 512), (768, 768), (1024, 1024),
    (1536, 1536), (2048, 2048), (4096, 4096),

    # -------------------------
    # 1:1 Square (General)
    # -------------------------
    (300, 300), (500, 500), (800, 800), (1000, 1000),
    (1500, 1500), (2000, 2000), (2500, 2500), (3000, 3000),

    # -------------------------
    # 4:3 Aspect (Classic Screens)
    # -------------------------
    (320, 240), (640, 480), (800, 600), (960, 720),
    (1024, 768), (1280, 960), (1600, 1200), (2048, 1536),
    (2560, 1920), (3200, 2400), (4096, 3072),

    # -------------------------
    # 3:2 Aspect (Photography)
    # -------------------------
    (600, 400), (900, 600), (1200, 800), (1500, 1000),
    (1800, 1200), (2400, 1600), (3000, 2000), (3600, 2400),
    (4500, 3000), (6000, 4000),

    # -------------------------
    # 16:9 Aspect (Video / Web)
    # -------------------------
    (426, 240), (640, 360), (854, 480), (960, 540),
    (1280, 720), (1366, 768), (1600, 900), (1920, 1080),
    (2560, 1440), (3200, 1800), (3440, 1935), (3840, 2160),
    (5120, 2880), (7680, 4320), (10240, 5760),

    # -------------------------
    # 16:10 Aspect (Laptops)
    # -------------------------
    (1280, 800), (1440, 900), (1680, 1050),
    (1920, 1200), (2560, 1600), (2880, 1800),
    (3840, 2400), (5120, 3200),

    # -------------------------
    # Mobile Portrait (9:16)
    # -------------------------
    (720, 1280), (1080, 1920), (1440, 2560),
    (2160, 3840), (1440, 2960), (1080, 2400),
    (1170, 2532), (1284, 2778), (1290, 2796),

    # -------------------------
    # Social Media Sizes
    # -------------------------
    # Instagram
    (1080, 1080), (1080, 1350), (1080, 1920),

    # Facebook
    (1200, 628), (1200, 1200),

    # Twitter/X
    (1200, 675), (1500, 500),

    # YouTube Thumbnails
    (1280, 720), (1920, 1080),

    # -------------------------
    # Banners / Headers
    # -------------------------
    (1600, 500), (1920, 500), (2560, 700),
    (3000, 1000), (3840, 1200),

    # -------------------------
    # Print-like Digital Sizes
    # -------------------------
    (2550, 3300),  # Letter @ 300 DPI
    (2480, 3508),  # A4 @ 300 DPI
    (3508, 4961),  # A3 @ 300 DPI
    (4961, 7016),  # A2 @ 300 DPI
    (7016, 9933),  # A1 @ 300 DPI

]


def write_recovered_png(
        png_bytes: bytes,
        ihdr_offset: int,
        width: int,
        height: int,
        output_path: Path,
) -> str:
    """
    Rebuilds a PNG by patching IHDR width/height and writes it to disk.
    Returns the output filename.
    """
    height_bytes = height.to_bytes(4, byteorder="big")
    width_bytes = width.to_bytes(4, byteorder="big")
    output_filename = f"recovered_{width}x{height}.png"

    # Splicing: [Start...IHDR+4] + [W] + [H] + [IHDR+12...End]
    full_png_data = (
            png_bytes[:ihdr_offset + 4]
            + width_bytes
            + height_bytes
            + png_bytes[ihdr_offset + 12:]
    )

    with output_path.open("wb") as out_f:
        out_f.write(full_png_data)

    return output_filename


def calc_checksum(header_chunk, width_bytes, height_bytes):
    """
    Calculates the CRC32 of the IHDR chunk with new dimensions.
    """
    new_header = header_chunk[:4] + width_bytes + height_bytes + header_chunk[12:]
    return bytearray((zlib.crc32(new_header) & 0xffffffff).to_bytes(4, byteorder='big'))


def lookup_crc(crc_bytes: bytes, logs: list) -> list:
    """
    Queries the SQLite DB for the CRC and returns a list of (width, height) tuples.
    """
    # Locate DB relative to this script file
    db_path = Path(__file__).parent / "ihdr_crcs.db"

    if not db_path.exists():
        logs.append(f"Error: Database not found at {db_path}")
        return []

    try:
        target_crc = int.from_bytes(crc_bytes, byteorder="big")
    except ValueError:
        logs.append(f"Invalid CRC bytes: {crc_bytes}")
        return []

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Query the database
    cursor.execute("SELECT width, height FROM ihdr WHERE crc = ?", (target_crc,))
    results = cursor.fetchall()  # Returns list of (width, height)
    conn.close()

    if results:
        logs.append(f"Database: Found {len(results)} match(es) for CRC {target_crc}")
        return results
    else:
        logs.append(f"Database: No match found for CRC {target_crc}")
        return []


def analyze_image_resize(input_img: Path, output_dir: Path) -> None:
    """
    Analyze an image submission using python-based resize bruteforce.
    Strategy: Check common sizes first (fast), then fall back to DB lookup (complete).
    """
    input_img = Path(input_img)
    output_dir = Path(output_dir)
    extracted_dir = output_dir / "image_resize_output_dir"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    logs = []
    recovered_image = []  # FIXED: Initialize as list, not None

    try:
        with open(input_img, "rb") as image:
            f = image.read()
        b = bytearray(f)

        # Find IHDR start
        IHDR = b.find(b'\x49\x48\x44\x52')
        if IHDR == -1:
            logs.append("Failure: PNG header (IHDR) not found.")
            update_data(output_dir, {"image_resize": {"status": "error", "error": "PNG header not found."}})
            return

        # Calculate Chunk Length
        chunk_length = int.from_bytes((b[IHDR - 4: IHDR]), byteorder="big") + 4

        # Extract Target CRC
        target_crc_bytes = b[IHDR + chunk_length: IHDR + chunk_length + 4]

        # Isolate the header chunk (Type + Data)
        header_chunk = b[IHDR: IHDR + chunk_length]

        logs.append(f"Target CRC found: 0x{target_crc_bytes.hex()}")

        match_found = False
        candidates = []

        # --- STRATEGY 1: Check Common Sizes ---
        for size in EXPECTED_SIZES:
            width, height = size
            w_bytes = bytearray(width.to_bytes(4, byteorder='big'))
            h_bytes = bytearray(height.to_bytes(4, byteorder='big'))

            if target_crc_bytes == calc_checksum(header_chunk, w_bytes, h_bytes):
                logs.append(f"Success (Common List): Match found! Dimensions: {width}x{height}")
                candidates.append((width, height))
                match_found = True
                break

        # --- STRATEGY 2: Check SQLite DB ---
        if not match_found:
            logs.append("No common size matched. Checking database...")
            db_matches = lookup_crc(target_crc_bytes, logs)
            if db_matches:
                candidates.extend(db_matches)
                match_found = True

        # --- Generate Output Images ---
        if match_found:
            for w, h in candidates:
                output_path = extracted_dir / f"recovered_{w}x{h}.png"
                filename = write_recovered_png(b, IHDR, w, h, output_path)
                recovered_image.append(filename)
        else:
            logs.append("Failure: No matching dimensions found in List or DB.")

        # Final Update
        output_data = {
            "image_resize": {
                "status": "ok",
                "output": logs,
                "images": recovered_image,
            }
        }
        update_data(output_dir, output_data)

    except Exception as e:
        # Catch-all for safety
        import traceback
        traceback.print_exc()
        update_data(output_dir, {"image_resize": {"status": "error", "error": str(e)}})
        if extracted_dir.exists():
            shutil.rmtree(extracted_dir, ignore_errors=True)

    return None