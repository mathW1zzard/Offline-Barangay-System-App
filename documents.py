"""Issuing documents: reading the issue form, the fee line, the frozen snapshot and the history
date range. No web code and no database code, so it can be tested on its own."""
import json
from datetime import date, timedelta

import doc_templates

CIVIL_STATUSES = ("Single", "Married", "Widowed", "Separated")
DEFAULT_NATIONALITY = "Filipino"
BLANK_LINE = "________________"          # printed for a CTC detail left empty, to be filled in by hand
MIN_DATE = date(2000, 1, 1)
OR_ENTER, OR_NONE = "enter", "none"          # the two choices when a fee was charged


class IssueError(ValueError):
    """The message is written for the secretary and is safe to show as it is."""


def _whole_number(text: str, low: int, high: int, message: str) -> int:
    cleaned = (text or "").strip()
    if not cleaned.isdigit() or not low <= int(cleaned) <= high:
        raise IssueError(message)
    return int(cleaned)


def parse_issue_date(text: str, today: date) -> date:
    try:
        chosen = date.fromisoformat((text or "").strip())
    except ValueError:
        raise IssueError("Please choose a valid date.") from None
    if chosen > today:
        raise IssueError("The date cannot be in the future.")
    if chosen < MIN_DATE:
        raise IssueError("Please choose a valid date.")
    return chosen


def read_issue_form(raw: dict, *, body: str, extras: list, today: date) -> dict:
    """Check what was typed. `raw` holds already-cleaned strings. Raises IssueError with a sentence
    for the secretary, or returns the values ready to use."""
    used = doc_templates.fields_used(body)
    if not raw["client_name"]:
        raise IssueError("Please enter the client's name.")
    if "Address" in used and not raw["address"]:
        raise IssueError("Please enter the address. This document prints it.")
    if "Purpose" in used and not raw["purpose"]:
        raise IssueError("Please enter the purpose of the document.")
    issue_date = parse_issue_date(raw["issue_date"], today)

    extra_values = {}
    if "age" in extras:
        extra_values["age"] = str(_whole_number(raw["age"], 1, 120, "Please enter the age as a number from 1 to 120."))
    if "civil_status" in extras:
        if raw["civil_status"] not in CIVIL_STATUSES:
            raise IssueError("Please choose the civil status.")
        extra_values["civil_status"] = raw["civil_status"]
    if "nationality" in extras:
        if not raw["nationality"]:
            raise IssueError("Please enter the nationality, for example Filipino.")
        extra_values["nationality"] = raw["nationality"]
    if "years" in extras:
        extra_values["years"] = str(_whole_number(raw["years"], 0, 120,
                                                  "Please enter the years of residence as a number from 0 to 120."))
    if "ctc" in extras:                       # Community Tax Certificate: each part may be left empty
        extra_values["ctc_number"] = raw["ctc_number"]
        extra_values["ctc_place"] = raw["ctc_place"]
        extra_values["ctc_date"] = ""
        if raw["ctc_date"]:
            try:
                ctc_day = date.fromisoformat(raw["ctc_date"])
            except ValueError:
                raise IssueError("Please enter a valid date for the CTC (Issued on).") from None
            if ctc_day > today or ctc_day < MIN_DATE:
                raise IssueError("The CTC date (Issued on) cannot be in the future.")
            extra_values["ctc_date"] = ctc_day.isoformat()
    signers = raw["signers"]
    if signers not in doc_templates.SIGNER_CHOICES:
        raise IssueError("Choose who signs this document.")
    try:
        fee = doc_templates.parse_fee(raw["fee"])
    except ValueError as e:
        raise IssueError(str(e)) from None

    or_status, or_number = "no_fee", None          # no fee: the receipt question is not asked
    if fee > 0:
        if raw["or_choice"] == OR_ENTER:
            if not raw["or_number"]:
                raise IssueError('Enter the official receipt number, or choose "Official Receipt not available".')
            or_status, or_number = "provided", raw["or_number"]
        elif raw["or_choice"] == OR_NONE:
            or_status = "not_available"
        else:
            raise IssueError('Choose "Enter OR number" or "Official Receipt not available".')
    return {"client_name": raw["client_name"], "address": raw["address"], "purpose": raw["purpose"],
            "issue_date": issue_date, "extras": extra_values, "fee_centavos": fee,
            "or_status": or_status, "or_number": or_number, "signers": signers}


def values_for(entry: dict, branding: dict, validity_months, control_number: str = "") -> dict:
    """Field -> printed text, from a checked form (or from a saved record). The control number is
    passed only when issuing (it is created first, then the wording is filled in)."""
    extras = entry["extras"]
    return doc_templates.field_values(
        client_name=entry["client_name"], address=entry["address"] or "", purpose=entry["purpose"] or "",
        issue_date=entry["issue_date"], barangay_name=branding["name"], municipality=branding["municipality"],
        province=branding["province"], age=extras.get("age", ""), civil_status=extras.get("civil_status", ""),
        nationality=extras.get("nationality", ""), years=extras.get("years", ""), fee_centavos=entry["fee_centavos"],
        or_number=entry["or_number"] or "", validity_months=validity_months, control_number=control_number,
        ctc_number=extras.get("ctc_number") or BLANK_LINE, ctc_place=extras.get("ctc_place") or BLANK_LINE,
        ctc_date=_us_date(extras.get("ctc_date", "")))


def _us_date(text: str) -> str:
    """'2025-12-31' -> '12/31/2025' (how the CTC date prints); empty -> a blank line. A value that is
    not a date (the preview's [CTC issued on]) is printed as it is."""
    try:
        day = date.fromisoformat(text)
    except (TypeError, ValueError):
        return text or BLANK_LINE
    return f"{day.month:02d}/{day.day:02d}/{day.year}"


def fee_line(fee_centavos: int, or_status: str, or_number, print_fee: bool):
    """The line at the bottom of a document. Nothing when there is no fee. With no receipt the
    fee prints without an OR line (the status stays in the records)."""
    if not print_fee or fee_centavos <= 0:
        return None
    line = f"Fee: {doc_templates.format_pesos(fee_centavos)}"
    if or_status == "provided" and or_number:
        line += f"      O.R. No.: {or_number}"
    return line


# Who signs, as (snapshot key prefix, ...) in the order the signature lines print, left to right
SIGNER_LINES = {"both": ("secretary", "captain"), "captain": ("captain",), "secretary": ("secretary",)}


def missing_signers(branding: dict, signers: str) -> list:
    """The titles of the chosen signers whose names are not set yet ([] when all are set)."""
    return [branding[f"{who}_title"] or who.title() for who in SIGNER_LINES.get(signers, ())
            if not branding[f"{who}_name"]]


# ---------------------------------------------------------------- the design of the printed page (step 8b)
# A design is read from a Word file or from a picture of the whole blank page (see docx_design.py
# and main.import_design). It is kept as a small JSON text on the template and frozen on every issued
# document. No design (None) is a plain page, exactly as documents printed before step 8b.
PLAIN = {"source": None, "file_name": "", "page_w": 215.9, "page_h": 279.4, "items": [], "layer": "word",
         "header": "show", "top_mm": 15, "bottom_mm": 15, "left_mm": 18, "right_mm": 18}
DESIGN_MM = (5, 150)                     # the smallest and largest space above / below the text
MAX_DESIGN_ITEMS = 40
LAYERS = {"word": "As in the Word file", "behind": "All behind the text", "front": "All in front of the text"}

# Sizes of the wording, in half points like Word (21 = 10.5 pt, the size used so far)
FONT_SIZES = {18: "9", 19: "9.5", 20: "10", 21: "10.5", 22: "11", 23: "11.5", 24: "12", 26: "13", 28: "14", 32: "16"}
DEFAULT_FONT = 21

# Spacing of the wording (step 8c): line spacing like Word, and the space after each paragraph
LINE_SPACINGS = {100: "1.0", 115: "1.15", 150: "1.5", 200: "2.0"}
PARA_GAPS = {"none": ("None", 0.0), "small": ("Small", 0.5), "normal": ("Normal", 1.0)}   # label, size in em
# How documents issued before step 8c were spaced: their reprints keep it
OLD_LOOK = {"line": 1.85, "gap": 1.15, "indent": True}


def parse_spacing(line_spacing: str, para_gap: str):
    """(line spacing, paragraph gap) from the editor. Raises ValueError with a sentence for the secretary."""
    text = (line_spacing or "").strip()
    if not text.isdigit() or int(text) not in LINE_SPACINGS or para_gap not in PARA_GAPS:
        raise ValueError("Please choose the line spacing and the space after each paragraph from the lists.")
    return int(text), para_gap


def look(font_half_pt: int, line_spacing: int, para_gap: str) -> dict:
    """How the wording is spaced on paper: text size (half points), line height, paragraph gap (em)."""
    return {"pt": font_half_pt, "line": line_spacing / 100, "gap": PARA_GAPS[para_gap][1], "indent": False}


def parse_font(text: str) -> int:
    value = (text or "").strip()
    if not value.isdigit() or int(value) not in FONT_SIZES:
        raise ValueError("Please choose a text size from the list.")
    return int(value)


def _number(value, low, high, what, digits=2):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ValueError(f"The design has a value that is out of range ({what}). Please load the design again.")
    return round(float(value), digits)


def parse_design(text: str):
    """The design as sent by the Templates editor (JSON text) -> a checked design, or None for a
    plain page. Everything is checked, because the text comes from the browser. Raises ValueError
    with a sentence for the secretary. Whether the pictures exist is checked by the caller."""
    import json
    if not (text or "").strip():
        return None
    try:
        raw = json.loads(text)
    except ValueError:
        raise ValueError("The design could not be read. Please load it again.") from None
    if not isinstance(raw, dict) or set(raw) - set(PLAIN):
        raise ValueError("The design could not be read. Please load it again.")
    if raw.get("source") not in ("docx", "picture"):
        raise ValueError("The design could not be read. Please load it again.")
    items = raw.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_DESIGN_ITEMS:
        raise ValueError("The design has no pictures. Please load it again.")
    checked = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"picture", "anchor", "x", "y", "w", "h", "crop", "front"}:
            raise ValueError("The design could not be read. Please load it again.")
        picture = item["picture"]
        if isinstance(picture, bool) or not isinstance(picture, int) or picture <= 0:
            raise ValueError("The design could not be read. Please load it again.")
        if item["anchor"] not in ("top", "bottom") or not isinstance(item["front"], bool):
            raise ValueError("The design could not be read. Please load it again.")
        crop = item["crop"]
        if not isinstance(crop, list) or len(crop) != 4:
            raise ValueError("The design could not be read. Please load it again.")
        crop = [_number(c, 0, 0.9, "crop", digits=4) for c in crop]
        if crop[0] + crop[2] >= 0.95 or crop[1] + crop[3] >= 0.95:
            raise ValueError("The design has a value that is out of range (crop). Please load the design again.")
        checked.append({"picture": picture, "anchor": item["anchor"], "x": _number(item["x"], -300, 700, "position"),
                        "y": _number(item["y"], -300, 700, "position"), "w": _number(item["w"], 1, 800, "size"),
                        "h": _number(item["h"], 1, 800, "size"), "crop": crop, "front": item["front"]})
    name = raw.get("file_name", "")
    if not isinstance(name, str):
        raise ValueError("The design could not be read. Please load it again.")
    design = {"source": raw["source"], "file_name": " ".join(name.split())[:120],
              "page_w": _number(raw.get("page_w"), 50, 500, "paper width"),
              "page_h": _number(raw.get("page_h"), 50, 700, "paper height"), "items": checked,
              "layer": raw.get("layer") if raw.get("layer") in LAYERS else "word",
              "header": raw.get("header") if raw.get("header") in ("show", "hide") else "show",
              "left_mm": _number(raw.get("left_mm", 18), 5, 60, "side margin"),
              "right_mm": _number(raw.get("right_mm", 18), 5, 60, "side margin")}
    low, high = DESIGN_MM
    for key, label in (("top_mm", "above"), ("bottom_mm", "below")):
        value = raw.get(key)
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"The space {label} the text must be a number from {low} to {high} mm.")
        design[key] = value
    return design


def pictures_of(design) -> list:
    return [item["picture"] for item in design["items"]] if design else []


def for_print(design) -> dict:
    """What the print page and the previews draw: the design (or a plain page), with each picture's
    final layer worked out from the layer setting."""
    full = dict(design or PLAIN)
    full["items"] = [{**item, "front": item["front"] if full["layer"] == "word" else full["layer"] == "front"}
                     for item in full["items"]]
    return full


def design_changes(old, new) -> list:
    """What changed in a design, in words for the audit log."""
    if old == new:
        return []
    if new is None:
        return ["design removed"]
    what = "Word file" if new["source"] == "docx" else "picture of the page"
    if old is None or pictures_of(old) != pictures_of(new) or old["file_name"] != new["file_name"]:
        name = f' "{new["file_name"]}"' if new["file_name"] else ""
        return [f"design loaded from {what}{name} ({len(new['items'])} picture(s))"]
    changes = []
    if old["layer"] != new["layer"]:
        changes.append("pictures: " + LAYERS[new["layer"]].lower())
    if old["header"] != new["header"]:
        changes.append("Settings header " + ("shown" if new["header"] == "show" else "hidden"))
    if (old["top_mm"], old["bottom_mm"]) != (new["top_mm"], new["bottom_mm"]):
        changes.append(f"space {new['top_mm']} mm above, {new['bottom_mm']} mm below")
    return changes or ["design adjusted"]


def make_snapshot(branding: dict, print_fee: bool, *, signers: str = "both", design: dict | None = None,
                  text_look: dict | None = None) -> dict:
    """What a reprint must not take from today's Settings or template: names, titles and header lines
    as they were when the document was issued, who signed, the indent rule and the design (its
    pictures never change, see pictures.py). (Logos stay current.)
    number_in_text marks documents issued since step 8a: their control number is part of the saved
    wording (where the template put it), so the print page does not add its own fixed line."""
    keys = ("full_name", "header_line_1", "header_line_2", "name", "place", "office_color", "captain_name",
            "captain_title", "secretary_name", "secretary_title")
    text_look = text_look or look(DEFAULT_FONT, 115, "small")
    return {**{k: branding[k] for k in keys}, "print_fee": bool(print_fee), "signers": signers,
            "auto_indent": False, "number_in_text": True, "design": design,
            "font_half_pt": text_look["pt"], "line": text_look["line"], "gap": text_look["gap"]}


def snapshot_look(snapshot: dict) -> dict:
    """The spacing a document was printed with. Before step 8c: the old spacing (and the automatic
    indent, if the template had it)."""
    if "line" in snapshot:
        return {"pt": snapshot.get("font_half_pt", DEFAULT_FONT), "line": snapshot["line"], "gap": snapshot["gap"],
                "indent": False}
    return {**OLD_LOOK, "pt": snapshot.get("font_half_pt", DEFAULT_FONT), "indent": snapshot.get("auto_indent", True)}


def snapshot_design(snapshot: dict):
    """The design a document was issued with (None = plain). Documents issued before step 8b have none."""
    design = snapshot.get("design")
    return design if isinstance(design, dict) and design.get("items") else None


def to_json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def from_json(text, fallback):
    try:
        return json.loads(text) if text else fallback
    except ValueError:
        return fallback


def history_range(date_from: str, date_to: str, today: date):
    """Nothing given -> the last 30 days. Dates entered backwards are swapped."""
    def parse(text):
        try:
            return date.fromisoformat(text) if text else None
        except ValueError:
            return None
    d_to = parse(date_to) or today
    d_from = parse(date_from) or d_to - timedelta(days=29)
    return (d_to, d_from) if d_to < d_from else (d_from, d_to)


def pesos_plain(centavos: int) -> str:
    """For the CSV: 5000 -> '50.00' (no currency sign, so a spreadsheet can add it up)."""
    return f"{centavos // 100}.{centavos % 100:02d}"