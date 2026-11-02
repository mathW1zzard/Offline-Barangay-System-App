"""Barangay profile, logos, signatories and document defaults (everything the Settings page edits).

This file has no web code, so it can be tested on its own. Settings live in the key-value
`settings` table; a key that is missing means "use DEFAULTS".
"""
import re
from pathlib import Path

import pictures

from app_logging import log
from database import SessionLocal, Setting
from paths import resource_path

# ---------------------------------------------------------------- settings keys and defaults
DEFAULTS = {
    # Barangay profile
    "barangay_name": "",
    "municipality": "",
    "province": "",
    "header_line_1": "Republic of the Philippines",
    "header_line_2": "",
    "office_color": "#C62828",    # the colour of the office line (header_line_2), step 8c
    # Logos: the number is a version stamp; empty means "no logo in this slot"
    "logo_left_pic": "",          # the number of the picture in the pictures table ("" = no logo)
    "logo_right_pic": "",
    "logo_third_pic": "",
    # Signatories
    "signatory_captain_name": "",
    "signatory_captain_title": "Barangay Captain",
    "signatory_secretary_name": "",
    "signatory_secretary_title": "Barangay Secretary",
    # Document defaults
    "control_number_format": "PREFIX-YEAR-NNNN",
    # Housekeeping
    "setup_complete": "",
    "legacy_logos_pending": "",
    "wizard_step": "profile",     # where the first-run wizard resumes: profile / logos / signatories / finish
    "wizard_later": "", 
}


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
# The colour of the office line ("OFFICE OF THE PUNONG BARANGAY"): a few ready choices, or any colour
OFFICE_COLORS = {"#C62828": "Red", "#800000": "Maroon", "#1B2A4A": "Navy", "#1565C0": "Blue",
                 "#2E7D32": "Green", "#111111": "Black"}
_COLOR = re.compile(r"#[0-9A-F]{6}")


def clean_color(text: str) -> str:
    """'#c62828' -> '#C62828'; anything that is not a colour -> '' (refused by validate_profile)."""
    value = (text or "").strip().upper()
    return value if _COLOR.fullmatch(value) else ""


def validate_profile(v: dict) -> str | None:
    required = (("barangay_name", "Barangay name"), ("municipality", "Municipality or city"), ("province", "Province"))
    missing = [label for key, label in required if not v.get(key)]
    if missing:
        return "Please fill in: " + ", ".join(missing) + "."
    if "office_color" in v and not _COLOR.fullmatch(v["office_color"]):
        return "Please choose a colour for the office line."
    return None


def validate_signatories(v: dict, require_names: bool = False) -> str | None:
    if not v["signatory_captain_title"] or not v["signatory_secretary_title"]:
        return "Each signatory needs a title, such as Barangay Captain."
    if require_names and (not v["signatory_captain_name"] or not v["signatory_secretary_name"]):
        return 'Please fill in both names, or press "Do this later".'
    return None


def validate_documents(v: dict) -> str | None:
    if v["control_number_format"] not in CONTROL_FORMATS:
        return "Please choose one of the options in the list."
    return None

# ---------------------------------------------------------------- logos
LOGO_SLOTS = ("left", "right", "third")
LOGO_LABELS = {"left": "Left logo", "right": "Right logo", "third": "Third logo"}
MAX_LOGO_BYTES = 2 * 1024 * 1024        # about 2 MB
LOGO_MAX_SIDE = 600                     # pixels on the longest side


class LogoError(Exception):
    """The message is written for the secretary and is safe to show as it is."""


def process_logo(raw: bytes):
    """Check an uploaded picture and return (png, width, height), small and see-through where it was.
    Raises LogoError with a friendly message."""
    try:
        png, _, width, height = pictures.prepare(raw, max_bytes=MAX_LOGO_BYTES, max_side=LOGO_MAX_SIDE, force_png=True)
    except pictures.PictureError as e:
        raise LogoError(str(e)) from None
    return png, width, height


def logo_picture_id(slot: str, values: dict | None = None):
    """The picture number of a logo slot, or None when the slot is empty."""
    v = load_settings() if values is None else values
    text = v.get(f"logo_{slot}_pic", "")
    return int(text) if text.isdigit() else None


def apply_logo_changes(uploads: dict, removals: set) -> list:
    """uploads: {slot: raw bytes}; removals: slots to empty. All or nothing: if ANY picture is
    rejected, nothing is changed. Returns a readable list of what changed (for the audit log).
    The pictures are kept inside the database (see pictures.py), so backups include them."""
    ready = {}
    for slot, raw in uploads.items():
        try:
            ready[slot] = process_logo(raw)
        except LogoError as e:
            raise LogoError(f"{LOGO_LABELS[slot]}: {e}") from None

    values = load_settings()
    new, changes = {}, []
    for slot, (png, width, height) in ready.items():
        changes.append(f"{LOGO_LABELS[slot]} {'replaced' if logo_picture_id(slot, values) else 'added'}")
        new[f"logo_{slot}_pic"] = str(pictures.store(png, "image/png", width, height))
    for slot in sorted(removals - set(ready)):      # a new picture wins over "Remove"
        if logo_picture_id(slot, values):
            new[f"logo_{slot}_pic"] = ""
            changes.append(f"{LOGO_LABELS[slot]} removed")
    if new:
        save_settings(new)
    return changes

def _logo_url(slot: str, values: dict) -> str | None:
    picture_id = logo_picture_id(slot, values)
    # ?v= changes with every new picture, so the browser shows a replaced logo at once
    return f"/branding/{slot}.png?v={picture_id}" if picture_id else None


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
            if logo_picture_id(slot, values) or not source.exists():
                continue
            try:
                png, width, height = process_logo(source.read_bytes())
                versions[f"logo_{slot}_pic"] = str(pictures.store(png, "image/png", width, height))
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
        "name": v["barangay_name"] or "Barangay name not set",
        # The header's second line (step 8c): "Municipality or city, Province", as typed in Settings
        "place": ", ".join(p for p in (v["municipality"], v["province"]) if p),
        "office_color": v["office_color"] if _COLOR.fullmatch(v["office_color"] or "") else DEFAULTS["office_color"],
        "municipality": v["municipality"],
        "province": v["province"],
        "header_line_1": v["header_line_1"],
        "header_line_2": v["header_line_2"],
        "logos": {slot: _logo_url(slot, v) for slot in LOGO_SLOTS},
        "captain_name": v["signatory_captain_name"],
        "captain_title": v["signatory_captain_title"],
        "secretary_name": v["signatory_secretary_name"],
        "secretary_title": v["signatory_secretary_title"],
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