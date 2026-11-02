"""The wording of documents: the built-in texts, the fields a text may use, and how a text becomes
paragraphs. No web code and no database code, so it can be tested on its own.

A template body is plain text with fields in curly brackets, like {Client name}. Fields are replaced
one by one with plain text; the body is NEVER run as code. A few marks are understood as well:
**double asterisks** around words print them in bold, a Tab prints a fixed gap (an indent), and a
line that starts with >> is centred (>>> : on the right). A blank line starts a new paragraph; one
Enter starts a new line in the same paragraph.
"""
import calendar
import difflib
import re
from datetime import date
from decimal import Decimal, InvalidOperation

MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")     # fixed, so a Windows language setting cannot change them

# ---------------------------------------------------------------- the fields a text may use
CORE_FIELDS = ("Client name", "Address", "Purpose", "Issue date", "Issue day", "Issue month", "Issue year",
               "Barangay name", "Municipality", "Province", "Control number")
CONTROL_FIELD = "Control number"        # filled in when the document is issued (the number does not exist before)

# The fixed menu of extras: key -> (label on the form, fields it adds). OR number and fee come as a pair.
EXTRA_MENU = {
    "age": ("Age", ("Age",)),
    "civil_status": ("Civil status", ("Civil status",)),
    "nationality": ("Nationality", ("Nationality",)),
    "years": ("Years of residence", ("Years of residence",)),
    "ctc": ("CTC (Community Tax Certificate)", ("CTC number", "CTC issued at", "CTC issued on")),
    "fee": ("Fee and OR number", ("Fee", "OR number")),
}
VALIDITY_FIELD = "Valid until"          # available only when the template has a validity in months

_OWNER = {field: key for key, (_, fields) in EXTRA_MENU.items() for field in fields}   # "Age" -> "age"
ALL_FIELDS = CORE_FIELDS + tuple(_OWNER) + (VALIDITY_FIELD,)

MAX_BODY_CHARS = 5000
TOKEN = re.compile(r"\{([^{}\n]*)\}")
BOLD_MARK = "**"
TAB = "\t"                             # a frozen piece of exactly this text prints as a fixed gap
BREAK = "\n"                           # ... a new line in the same paragraph (step 8c)
CENTER = "\x01center"                  # ... at the start of a line: that line is centred
RIGHT = "\x01right"                    # ... at the start of a line: that line is on the right
CENTER_MARK, RIGHT_MARK = ">>", ">>>"   # what the secretary types (or the Center / Right buttons put) at the start of a line
_SPECIAL = {"\t": TAB, "\n": BREAK, "\x02": CENTER, "\x03": RIGHT}

# The line the v4 upgrade puts at the top of saved wordings, and that the built-in wordings start with
CONTROL_LINE = "Control No.: {Control number}"
SIGNER_CHOICES = ("both", "captain", "secretary")


def _norm(name: str) -> str:
    """Matching ignores capitals and extra spaces, so {client  NAME} still works."""
    return " ".join(name.split()).casefold()


def available_fields(extras, validity_on: bool) -> tuple:
    fields = list(CORE_FIELDS)
    for key in EXTRA_MENU:
        if key in extras:
            fields.extend(EXTRA_MENU[key][1])
    if validity_on:
        fields.append(VALIDITY_FIELD)
    return tuple(fields)


def clean_extras(values) -> list:
    """Only keys from the menu, each once, in menu order."""
    return [key for key in EXTRA_MENU if key in set(values)]

def fields_used(body: str) -> set:
    """The names of the fields a wording uses, e.g. {'Client name', 'Address'}. The issue form
    uses this to know what it must ask for."""
    everything = {_norm(f): f for f in ALL_FIELDS}
    return {everything[_norm(m.group(1))] for m in TOKEN.finditer(body) if _norm(m.group(1)) in everything}

def validate_body(body: str, available) -> str | None:
    """None if the text is fine, otherwise a sentence for the secretary."""
    if not body.strip():
        return "The wording cannot be empty."
    if len(body) > MAX_BODY_CHARS:
        return f"The wording is too long ({MAX_BODY_CHARS} characters at most)."
    if "{" in TOKEN.sub("", body) or "}" in TOKEN.sub("", body):
        return "There is a { or } that is not part of a field. Fields look like {Client name}."
    for chunk in _paragraph_chunks(body):
        if chunk.count(BOLD_MARK) % 2:
            start = " ".join(chunk.split())[:40]
            return (f'A bold mark ** is not closed in the paragraph that starts "{start}". '
                    "Bold text must start and end with ** in the same paragraph, like **this**.")
    allowed = {_norm(f) for f in available}
    everything = {_norm(f): f for f in ALL_FIELDS}
    for match in TOKEN.finditer(body):
        typed = match.group(1)
        key = _norm(typed)
        if key in allowed:
            continue
        if key in everything:                                    # a real field, but not switched on for this template
            field = everything[key]
            if field == VALIDITY_FIELD:
                return "{Valid until} is only available when \"This document expires\" is ticked."
            return f"{{{field}}} is only available when \"{EXTRA_MENU[_OWNER[field]][0]}\" is ticked under Extra fields."
        close = difflib.get_close_matches(key, list(everything), n=1, cutoff=0.6)
        hint = f" Did you mean {{{everything[close[0]]}}}?" if close else ""
        return f"{{{typed}}} is not a known field.{hint}"
    return None

# ---------------------------------------------------------------- from text to paragraphs
MAX_EXTRA_LINES = 5                     # empty lines kept between two paragraphs (more are dropped)


def normalize_body(text: str) -> str:
    """One newline style, no trailing spaces, at most MAX_EXTRA_LINES extra empty lines in a row. A Tab
    at the very start is kept (it is an indent the secretary typed), so only newlines and spaces are
    trimmed."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    most = MAX_EXTRA_LINES + 2
    return re.sub(r"\n{%d,}" % (most + 1), "\n" * most, text).strip("\n ")


def _paragraph_chunks(body: str) -> list:
    """A blank line starts a new paragraph. Each EXTRA blank line (step 8c) is an empty line of space,
    kept as a line break at the start of the next paragraph, so pressing Enter once more after
    "TO WHOM IT MAY CONCERN:" makes the space after it one line bigger."""
    chunks, extra = [], 0
    for chunk in re.split(r"\n[ \t]*\n", normalize_body(body)):
        if not chunk.strip():
            extra += 1                      # "\n\n\n\n" splits into "", ...: one empty piece per extra line pair
            continue
        lead = len(chunk) - len(chunk.lstrip("\n"))   # an odd extra blank line stays as a leading newline
        chunks.append("\n" * (2 * extra + lead) + chunk.lstrip("\n") if extra or lead else chunk)
        extra = 0
    return chunks


def _flatten(chunk: str) -> str:
    """One paragraph -> one string. Each line keeps its own place (an Enter is a new line, step 8c);
    inside a line, runs of spaces become one space. Tabs are kept, without the spaces around them.
    A line that starts with >>> or >> gets a hidden sign for "right" or "centre" in front."""
    lines = []
    for line in chunk.split("\n"):
        line = line.strip(" ")
        sign = ""
        if line.startswith(RIGHT_MARK):
            sign, line = "\x03", line[len(RIGHT_MARK):]
        elif line.startswith(CENTER_MARK):
            sign, line = "\x02", line[len(CENTER_MARK):]
        line = re.sub(r"[ \f\v\x01-\x03]+", " ", line).strip(" ")
        lines.append(sign + re.sub(r" *\t *", TAB, line))
    return "\n".join(lines)


def _add(pairs: list, text: str, bold: bool) -> None:
    """Add a piece, splitting out tabs, line breaks and alignment signs, and joining it to the
    previous piece when the style matches."""
    for part in re.split(r"(\t|\n|\x02|\x03)", text):
        if not part:
            continue
        if part in _SPECIAL:
            pairs.append([_SPECIAL[part], False])
        elif pairs and pairs[-1][1] == bold and pairs[-1][0] not in _SPECIAL.values():
            pairs[-1][0] += part
        else:
            pairs.append([part, bold])


def paragraph_lines(pairs: list) -> list:
    """A frozen paragraph -> its lines, each {"align": "left"/"center"/"right", "pieces": [[text, bold]]},
    for the print page. Documents issued before step 8c have one line, on the left."""
    lines = [{"align": "left", "pieces": []}]
    for text, bold in pairs:
        if text == BREAK:
            lines.append({"align": "left", "pieces": []})
        elif text == CENTER and not lines[-1]["pieces"]:
            lines[-1]["align"] = "center"
        elif text == RIGHT and not lines[-1]["pieces"]:
            lines[-1]["align"] = "right"
        elif text not in (CENTER, RIGHT):
            lines[-1]["pieces"].append([text, bold])
    return lines


def render_paragraphs(body: str, values: dict, auto_bold: bool = True) -> list:
    """Body + field values -> paragraphs, each a list of [text, bold] pairs. A piece whose text is
    exactly a Tab prints as a gap. Text between ** marks is bold; with auto_bold, filled-in values
    are bold too. An unknown {field} is left exactly as typed (saving is blocked on those, so this
    only shows while she is still typing). An unclosed ** (also blocked on saving) prints as typed.
    The same structure is what is frozen on an issued document. Values are inserted AFTER the bold
    marks are read, so ** typed by a client (in a name, say) never turns anything bold."""
    known = {_norm(k): v for k, v in values.items()}
    paragraphs = []
    for chunk in _paragraph_chunks(body):
        text = _flatten(chunk)
        segments = text.split(BOLD_MARK)
        if len(segments) % 2 == 0:                 # an odd number of marks: the last one is plain text
            segments[-2:] = [segments[-2] + BOLD_MARK + segments[-1]]
        pairs = []
        for index, segment in enumerate(segments):
            marked = index % 2 == 1                # between two marks
            last = 0
            for match in TOKEN.finditer(segment):
                key = _norm(match.group(1))
                if key not in known:
                    continue
                _add(pairs, segment[last:match.start()], marked)
                if known[key]:
                    _add(pairs, known[key], marked or auto_bold)
                last = match.end()
            _add(pairs, segment[last:], marked)
        if pairs:
            paragraphs.append(pairs)
    return paragraphs


def has_control_number(body: str) -> bool:
    return CONTROL_FIELD in fields_used(body or "")

# ---------------------------------------------------------------- field values
def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def add_months(start: date, months: int) -> date:
    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def format_pesos(centavos: int) -> str:
    return f"\u20b1{centavos // 100:,}.{centavos % 100:02d}"


def field_values(*, client_name="", address="", purpose="", issue_date: date, barangay_name="", municipality="",
                 province="", age="", civil_status="", nationality="", years="", fee_centavos=0, or_number="",
                 validity_months=None, control_number="", ctc_number="", ctc_place="", ctc_date="") -> dict:
    """Every field -> the text it prints. Used with sample data here and with real data when issuing."""
    values = {
        "Client name": client_name, "Address": address, "Purpose": purpose,
        "Issue date": f"{MONTHS[issue_date.month - 1]} {issue_date.day}, {issue_date.year}",
        "Issue day": ordinal(issue_date.day), "Issue month": MONTHS[issue_date.month - 1],
        "Issue year": str(issue_date.year),
        "Barangay name": barangay_name, "Municipality": municipality, "Province": province,
        "Age": age, "Civil status": civil_status, "Nationality": nationality, "Years of residence": years,
        "Control number": control_number,
        "CTC number": ctc_number, "CTC issued at": ctc_place, "CTC issued on": ctc_date,
        "Fee": format_pesos(fee_centavos) if fee_centavos > 0 else "No fee",
        "OR number": or_number,
    }
    if validity_months:
        until = add_months(issue_date, validity_months)
        values["Valid until"] = f"{MONTHS[until.month - 1]} {until.day}, {until.year}"
    return values


def sample_values(branding: dict, fee_centavos: int, validity_months, today: date, control_number="") -> dict:
    """What the live preview fills in. The barangay's own name is real; the client is made up."""
    return field_values(
        client_name="Juan Dela Cruz", address="Purok 1, Sample Street", purpose="employment",
        issue_date=today, barangay_name=branding["name"], municipality=branding["municipality"],
        province=branding["province"], age="35", civil_status="Single", nationality="Filipino", years="10",
        fee_centavos=fee_centavos, or_number="1234567", validity_months=validity_months,
        control_number=control_number, ctc_number="12345678", ctc_place=branding["municipality"] or "Sample City",
        ctc_date=f"{today.month:02d}/{today.day:02d}/{today.year}")


# ---------------------------------------------------------------- reading what was typed in the editor
def parse_fee(text: str) -> int:
    """'50', '50.5' or '1,000.00' -> centavos. Raises ValueError with a message for the secretary."""
    cleaned = (text or "").replace(",", "").replace("\u20b1", "").strip()
    if not cleaned:
        return 0
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError("Enter the fee as an amount such as 50 or 50.00.") from None
    if not amount.is_finite() or amount < 0 or amount > 100000:
        raise ValueError("The fee must be between 0 and 100,000 pesos.")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValueError("The fee can have at most two decimal places, such as 50.25.")
    return int(amount * 100)


def format_fee_input(centavos: int) -> str:
    return f"{centavos // 100}.{centavos % 100:02d}"


def parse_prefix(text: str) -> str:
    prefix = (text or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{1,6}", prefix):
        raise ValueError("Use 1 to 6 letters or numbers for the number prefix, for example BC.")
    return prefix


def parse_months(text: str) -> int:
    try:
        months = int((text or "").strip())
    except ValueError:
        months = 0
    if not 1 <= months <= 60:
        raise ValueError("Enter how many months the document stays valid (1 to 60).")
    return months

# ---------------------------------------------------------------- the built-in wordings
# English only for now. The key ends in a language code so Filipino wordings can be added later
# without a migration. A built-in row with no saved body uses the text below; Restore clears it.
# EACH BARANGAY MUST CONFIRM THESE WORDINGS against its own practice before using them.
_CLOSING = ("Issued this {Issue day} day of {Issue month} {Issue year} at {Barangay name}, "
            "{Municipality}, {Province}.")
_PURPOSE = ("This certification is issued upon the request of the above-named person for {Purpose} "
            "and for whatever legal purpose it may serve.")

BUILTINS = {
    "residency_en": {
        "name": "Certificate of Residency", "prefix": "CR",
        "body": (CONTROL_LINE + "\n\nTO WHOM IT MAY CONCERN:\n\n"
                 "This is to certify that {Client name} is a resident of {Address}, {Barangay name}, "
                 "{Municipality}, {Province}.\n\n" + _PURPOSE + "\n\n" + _CLOSING),
    },
    "clearance_en": {
        "name": "Barangay Clearance", "prefix": "BC",
        "body": (CONTROL_LINE + "\n\nTO WHOM IT MAY CONCERN:\n\n"
                 "This is to certify that {Client name}, a resident of {Address}, {Barangay name}, "
                 "{Municipality}, {Province}, has no derogatory record on file in this office as of this date.\n\n"
                 "This clearance is issued upon the request of the above-named person for {Purpose} "
                 "and for whatever legal purpose it may serve.\n\n" + _CLOSING),
    },
    "indigency_en": {
        "name": "Certificate of Indigency", "prefix": "CI",
        "body": (CONTROL_LINE + "\n\nTO WHOM IT MAY CONCERN:\n\n"
                 "This is to certify that {Client name}, a resident of {Address}, {Barangay name}, "
                 "{Municipality}, {Province}, belongs to an indigent family in this barangay.\n\n" + _PURPOSE
                 + "\n\n" + _CLOSING),
    },
    "blank_en": {
        "name": "Blank Document", "prefix": "DOC",
        "body": CONTROL_LINE + "\n\nTO WHOM IT MAY CONCERN:\n\n{Purpose}\n\n" + _CLOSING,
    },
}


def builtin_body(key: str) -> str:
    return BUILTINS.get(key, {}).get("body", "")