"""Issuing documents: reading the issue form, the fee line, the frozen snapshot and the history
date range. No web code and no database code, so it can be tested on its own."""
import json
from datetime import date, timedelta

import doc_templates

CIVIL_STATUSES = ("Single", "Married", "Widowed", "Separated")
DEFAULT_NATIONALITY = "Filipino"
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
    if "years" in extras:
        extra_values["years"] = str(_whole_number(raw["years"], 0, 120,
                                                  "Please enter the years of residence as a number from 0 to 120."))
    if "nationality" in extras:
        if not raw["nationality"]:
            raise IssueError("Please enter the nationality, for example Filipino.")
        extra_values["nationality"] = raw["nationality"]
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
            "or_status": or_status, "or_number": or_number}


def values_for(entry: dict, branding: dict, validity_months, control_number: str = "") -> dict:
    """Field -> printed text, from a checked form (or from a saved record). The control number is
    passed only when issuing (it is created first, then the wording is filled in)."""
    extras = entry["extras"]
    return doc_templates.field_values(
        client_name=entry["client_name"], address=entry["address"] or "", purpose=entry["purpose"] or "",
        issue_date=entry["issue_date"], barangay_name=branding["name"], municipality=branding["municipality"],
        province=branding["province"], age=extras.get("age", ""), civil_status=extras.get("civil_status", ""),
        nationality=extras.get("nationality", ""), years=extras.get("years", ""), fee_centavos=entry["fee_centavos"],
        or_number=entry["or_number"] or "", validity_months=validity_months, control_number=control_number)


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


def make_snapshot(branding: dict, print_fee: bool, *, signers: str = "both", auto_indent: bool = True) -> dict:
    """What a reprint must not take from today's Settings or template: names, titles and header lines
    as they were when the document was issued, who signed, and the indent rule. (Logos stay current.)
    number_in_text marks documents issued since step 8a: their control number is part of the saved
    wording (where the template put it), so the print page does not add its own fixed line."""
    keys = ("full_name", "header_line_1", "header_line_2", "captain_name", "captain_title",
            "secretary_name", "secretary_title")
    return {**{k: branding[k] for k in keys}, "print_fee": bool(print_fee), "signers": signers,
            "auto_indent": bool(auto_indent), "number_in_text": True}


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