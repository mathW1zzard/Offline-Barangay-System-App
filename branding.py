"""Barangay profile, logos, signatories and document defaults (everything the Settings page edits).

This file has no web code, so it can be tested on its own. Settings live in the key-value
`settings` table; a key that is missing means "use DEFAULTS".
"""
import io
import os
import time
from pathlib import Path

from PIL import Image, ImageOps

from app_logging import log
from database import SessionLocal, Setting
from paths import data_path, resource_path

# ---------------------------------------------------------------- settings keys and defaults
DEFAULTS = {
    # Barangay profile
    "barangay_name": "",
    "municipality": "",
    "province": "",
    "header_line_1": "Republic of the Philippines",
    "header_line_2": "",
    # Logos: the number is a version stamp; empty means "no logo in this slot"
    "logo_left_v": "",
    "logo_right_v": "",
    "logo_third_v": "",
    # Signatories
    "signatory_captain_name": "",
    "signatory_captain_title": "Barangay Captain",
    "signatory_secretary_name": "",
    "signatory_secretary_title": "Barangay Secretary",
    # Document defaults
    "paper_size": "A4",
    "control_number_format": "PREFIX-YEAR-NNNN",
    # Housekeeping
    "setup_complete": "",
    "legacy_logos_pending": "",
    "wizard_step": "profile",     # where the first-run wizard resumes: profile / logos / signatories / finish
    "wizard_later": "", 
}

PAPER_SIZES = {                     # what the secretary sees
    "A4": "A4 (210 x 297 mm)",
    "Letter": "Short bond / Letter (8.5 x 11 in)",
    "Long": "Long bond (8.5 x 13 in)",
}
PAPER_CSS = {"A4": "A4", "Letter": "letter", "Long": "8.5in 13in"}   # for the CSS "@page size" (used from step 5)


def format_control_number(style: str, prefix: str, year: int, number: int) -> str:
    """BC + 2026 + 7 -> 'BC-2026-0007' (the style decides the order and the digits). Used from step 5."""
    digits = 5 if style.endswith("NNNNN") else 4
    num = f"{number:0{digits}d}"
    return f"{year}-{prefix}-{num}" if style.startswith("YEAR") else f"{prefix}-{year}-{num}"


CONTROL_FORMATS = {style: format_control_number(style, "BC", 2026, 1)
                   for style in ("PREFIX-YEAR-NNNN", "PREFIX-YEAR-NNNNN", "YEAR-PREFIX-NNNN")}

# ---------------------------------------------------------------- reading and writing settings
def load_settings() -> dict:
    """Every setting, with DEFAULTS filling in whatever is not saved yet."""
    with SessionLocal() as db:
        rows = db.query(Setting).all()
        saved = {row.key: row.value for row in rows}
    return {**DEFAULTS, **saved}


def save_settings(values: dict) -> None:
    """Save several settings in one transaction: all of them or none."""
    with SessionLocal() as db:
        for key, value in values.items():
            row = db.get(Setting, key)
            if row is None:
                db.add(Setting(key=key, value=str(value)))
            else:
                row.value = str(value)
        db.commit()


# ---------------------------------------------------------------- checking what was typed
def validate_profile(v: dict) -> str | None:
    required = (("barangay_name", "Barangay name"), ("municipality", "Municipality or city"), ("province", "Province"))
    missing = [label for key, label in required if not v.get(key)]
    return ("Please fill in: " + ", ".join(missing) + ".") if missing else None


def validate_signatories(v: dict, require_names: bool = False) -> str | None:
    if not v["signatory_captain_title"] or not v["signatory_secretary_title"]:
        return "Each signatory needs a title, such as Barangay Captain."
    if require_names and (not v["signatory_captain_name"] or not v["signatory_secretary_name"]):
        return 'Please fill in both names, or press "Do this later".'
    return None


def validate_documents(v: dict) -> str | None:
    if v["paper_size"] not in PAPER_SIZES or v["control_number_format"] not in CONTROL_FORMATS:
        return "Please choose one of the options in the lists."
    return None

# ---------------------------------------------------------------- logos
LOGO_SLOTS = ("left", "right", "third")
LOGO_LABELS = {"left": "Left logo", "right": "Right logo", "third": "Third logo"}
MAX_LOGO_BYTES = 2 * 1024 * 1024        # about 2 MB
MAX_LOGO_PIXELS = 25_000_000            # refuses "decompression bombs" (tiny file, enormous picture)
LOGO_MAX_SIDE = 600                     # pixels on the longest side


class LogoError(Exception):
    """The message is written for the secretary and is safe to show as it is."""


def logo_path(slot: str) -> Path:
    return Path(data_path(f"logo_{slot}.png"))      # fixed names: the uploaded file name is never used


def _new_version() -> str:
    return str(time.time_ns() // 1_000_000)         # milliseconds: two saves never share a version


def process_logo(raw: bytes) -> bytes:
    """Check an uploaded picture and return it as a small PNG. Raises LogoError with a friendly message."""
    if len(raw) > MAX_LOGO_BYTES:
        raise LogoError("That picture is larger than 2 MB. Please choose a smaller one.")
    try:
        img = Image.open(io.BytesIO(raw), formats=["PNG", "JPEG", "WEBP"])
        if img.width * img.height > MAX_LOGO_PIXELS:
            raise LogoError("That picture is too large. Please choose a smaller one.")
        img = ImageOps.exif_transpose(img)          # undo the phone's rotation
        img = img.convert("RGBA")                   # keeps see-through backgrounds
        img.thumbnail((LOGO_MAX_SIDE, LOGO_MAX_SIDE))   # only ever shrinks
        out = io.BytesIO()
        img.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except LogoError:
        raise
    except Exception as e:
        raise LogoError("That file could not be read as a picture. Please choose a PNG or JPG file.") from e


def write_logo(slot: str, png: bytes) -> None:
    target = logo_path(slot)
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(png)
    os.replace(tmp, target)                         # atomic: never leaves a half-written logo


def apply_logo_changes(uploads: dict, removals: set) -> list:
    """uploads: {slot: raw bytes}; removals: slots to empty. All or nothing: if ANY picture is
    rejected, nothing is changed. Returns a readable list of what changed (for the audit log)."""
    ready = {}
    for slot, raw in uploads.items():
        try:
            ready[slot] = process_logo(raw)
        except LogoError as e:
            raise LogoError(f"{LOGO_LABELS[slot]}: {e}") from None

    versions, changes = {}, []
    for slot, png in ready.items():
        changes.append(f"{LOGO_LABELS[slot]} {'replaced' if logo_path(slot).exists() else 'added'}")
        write_logo(slot, png)
        versions[f"logo_{slot}_v"] = _new_version()
    for slot in sorted(removals - set(ready)):      # a new picture wins over "Remove"
        if logo_path(slot).exists():
            logo_path(slot).unlink()
            versions[f"logo_{slot}_v"] = ""
            changes.append(f"{LOGO_LABELS[slot]} removed")
    if versions:
        save_settings(versions)
    return changes

def _logo_url(slot: str, values: dict) -> str | None:
    version = values.get(f"logo_{slot}_v")
    if version and logo_path(slot).exists():
        return f"/branding/{slot}.png?v={version}"  # ?v= makes the browser load a replaced logo at once
    return None


def logo_info(values: dict) -> dict:
    return {slot: {"label": LOGO_LABELS[slot], "url": _logo_url(slot, values)} for slot in LOGO_SLOTS}


# The three logos the Barangay 4 edition shipped inside the program, and the slot each one goes to
LEGACY_LOGOS = {
    "left": "static/logos/barangay_4_logo.png",
    "right": "static/logos/san _mateo.png",
    "third": "static/logos/bagong_pilipinas logo.png",
}


def seed_legacy_logos() -> None:
    """Runs at every start-up but acts once: an installation upgraded from the Barangay 4 edition
    (migration v3 sets legacy_logos_pending) gets its three old logos copied into the new slots,
    so its printouts keep their header. Fresh installations never have the flag."""
    try:
        values = load_settings()
        if values["legacy_logos_pending"] != "1":
            return
        versions = {"legacy_logos_pending": "0"}
        for slot, relative in LEGACY_LOGOS.items():
            source = Path(resource_path(relative))
            if logo_path(slot).exists() or not source.exists():
                continue
            try:
                write_logo(slot, process_logo(source.read_bytes()))
                versions[f"logo_{slot}_v"] = _new_version()
            except Exception:
                log.exception("Could not copy the old %s logo", slot)
        save_settings(versions)
    except Exception:
        log.exception("Seeding the old logos failed")


# ---------------------------------------------------------------- what every printout and page reads
def get_branding(values: dict | None = None) -> dict:
    v = load_settings() if values is None else values
    parts = [v["barangay_name"], v["municipality"], v["province"]]
    return {
        "full_name": ", ".join(p for p in parts if p) or "Barangay name not set",
        "name": v["barangay_name"],
        "municipality": v["municipality"],
        "province": v["province"],
        "header_line_1": v["header_line_1"],
        "header_line_2": v["header_line_2"],
        "logos": {slot: _logo_url(slot, v) for slot in LOGO_SLOTS},
        "captain_name": v["signatory_captain_name"],
        "captain_title": v["signatory_captain_title"],
        "secretary_name": v["signatory_secretary_name"],
        "secretary_title": v["signatory_secretary_title"],
        "paper_size": v["paper_size"],
        "control_number_format": v["control_number_format"],
    }
    
# ---------------------------------------------------------------- first-run wizard
WIZARD_STEPS = ("profile", "logos", "signatories", "finish")

_setup_done = False             # once True it stays True, so normal pages skip a database read


def is_setup_complete() -> bool:
    global _setup_done
    if not _setup_done:
        _setup_done = load_settings()["setup_complete"] == "1"
    return _setup_done


def wizard_step() -> str:
    """The step to resume at (profile if nothing is saved or the saved value is unknown)."""
    step = load_settings()["wizard_step"]
    return step if step in WIZARD_STEPS else "profile"


def advance_wizard(to_step: str) -> None:
    """Move forward only: going Back to an earlier step and saving it must not lose progress."""
    if WIZARD_STEPS.index(to_step) > WIZARD_STEPS.index(wizard_step()):
        save_settings({"wizard_step": to_step})


def finish_setup() -> None:
    global _setup_done
    save_settings({"setup_complete": "1", "wizard_step": "finish"})
    _setup_done = True


def later_items(values: dict | None = None) -> set:
    """Wizard steps skipped with "Do this later" (Settings shows a notice for them)."""
    v = load_settings() if values is None else values
    return {item for item in v["wizard_later"].split(",") if item}


def mark_later(item: str) -> None:
    save_settings({"wizard_later": ",".join(sorted(later_items() | {item}))})


def clear_later(item: str) -> None:
    items = later_items()
    if item in items:
        save_settings({"wizard_later": ",".join(sorted(items - {item}))})