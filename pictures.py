"""Pictures kept INSIDE the database: the logos and the certificate designs (banners, watermark).

Why in the database: a backup is one copy of the database file, so the pictures are in every
backup and a restore on another computer brings them back.

A stored picture never changes. A new upload becomes a new row (the same picture uploaded twice
is stored once), and the app never deletes a row. So a document issued with a picture can always
be reprinted with exactly that picture, even after the design was changed.
"""
import hashlib
import io
from datetime import datetime

from PIL import Image, ImageOps
from sqlalchemy.exc import IntegrityError

from database import Picture, SessionLocal

MAX_PIXELS = 40_000_000                 # refuses "decompression bombs" (tiny file, enormous picture)
DESIGN_MAX_BYTES = 10 * 1024 * 1024     # what may be uploaded for a design (about 10 MB)
DESIGN_MAX_SIDE = 3000                  # pixels on the longest side once stored: sharp on long bond paper
SHARP_WIDTH = 1600                      # a banner narrower than this may look soft when printed across the page


class PictureError(Exception):
    """The message is written for the secretary and is safe to show as it is."""


def prepare(raw: bytes, *, max_bytes: int, max_side: int, force_png: bool = False):
    """Check an uploaded picture and re-save it cleanly (this also drops anything hidden in the file).
    Returns (data, mime, width, height). See-through pictures stay PNG; other pictures become JPEG,
    which is much smaller for photos and printed banners. Raises PictureError with a friendly message."""
    if len(raw) > max_bytes:
        raise PictureError(f"That picture is larger than {max_bytes // (1024 * 1024)} MB. Please choose a smaller one.")
    try:
        img = Image.open(io.BytesIO(raw), formats=["PNG", "JPEG", "WEBP"])
        if img.width * img.height > MAX_PIXELS:
            raise PictureError("That picture is too large. Please choose a smaller one.")
        img = ImageOps.exif_transpose(img)          # undo the phone's rotation
        img.thumbnail((max_side, max_side))         # only ever shrinks
        rgba = img.convert("RGBA")
        see_through = rgba.getchannel("A").getextrema()[0] < 255
        out = io.BytesIO()
        if force_png or see_through:
            rgba.save(out, format="PNG", optimize=True)
            mime = "image/png"
        else:
            rgba.convert("RGB").save(out, format="JPEG", quality=90, optimize=True)
            mime = "image/jpeg"
        return out.getvalue(), mime, rgba.width, rgba.height
    except PictureError:
        raise
    except Exception as e:
        raise PictureError("That file could not be read as a picture. Please choose a PNG or JPG file.") from e


def store(data: bytes, mime: str, width: int, height: int) -> int:
    """Save a prepared picture and return its number. The same picture twice gives the same number."""
    digest = hashlib.sha256(data).hexdigest()
    with SessionLocal() as db:
        found = db.query(Picture.id).filter(Picture.sha256 == digest).scalar()
        if found:
            return found
        picture = Picture(sha256=digest, mime=mime, data=data, width=width, height=height, created_at=datetime.now())
        db.add(picture)
        try:
            db.commit()
        except IntegrityError:                      # the same picture was saved a moment ago by another request
            db.rollback()
            return db.query(Picture.id).filter(Picture.sha256 == digest).scalar()
        return picture.id


def load(picture_id: int):
    """(data, mime) of a stored picture, or None."""
    with SessionLocal() as db:
        picture = db.get(Picture, picture_id)
        return (picture.data, picture.mime) if picture else None


def existing(db, ids) -> set:
    """Which of these picture numbers really exist."""
    wanted = {i for i in ids if i}
    if not wanted:
        return set()
    return {row[0] for row in db.query(Picture.id).filter(Picture.id.in_(wanted)).all()}
