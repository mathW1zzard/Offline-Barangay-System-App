"""Which app this build is: the full Barangay System, or the Attendance edition.

Both apps come from this same code. The Attendance edition is the queue and the visitor logbook for an
office run by one person: no certificates (Documents) and no staff accounts. Everything else (queue,
display, attendance, Settings, the first-run wizard, backups, audit log) is shared, so a fix made once
lands in both apps.

How the edition is chosen:
  * Installed app: the file edition.txt bundled next to the program by the .spec file
    (Barangay_Attendance.spec bundles edition_attendance.txt as edition.txt). No file = the full app.
  * Development: the environment variable BQS_EDITION=attendance (or full). The installed app ignores
    it, so a setting on an office PC can never turn one app into the other.

This file must not import paths.py or main.py: paths.py reads DATA_FOLDER from here.
"""
import os
import sys
from pathlib import Path

FULL = "full"
ATTENDANCE = "attendance"
EDITIONS = (FULL, ATTENDANCE)

FROZEN = getattr(sys, "frozen", False)
_BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _read_edition() -> str:
    if FROZEN:
        try:
            value = (_BUNDLE_DIR / "edition.txt").read_text(encoding="utf-8").strip().lower()
        except OSError:
            value = FULL                       # the full app's build bundles no edition file
    else:
        value = os.environ.get("BQS_EDITION", "").strip().lower() or FULL
    if value not in EDITIONS:
        # A typo must stop the app at once, not quietly build the wrong one
        raise ValueError(f"Unknown edition {value!r}; use one of {EDITIONS}")
    return value


EDITION = _read_edition()
IS_ATTENDANCE = EDITION == ATTENDANCE

# ---- what each app has
HAS_DOCUMENTS = not IS_ATTENDANCE       # certificates, issued documents, templates and designs
HAS_STAFF = not IS_ATTENDANCE           # staff accounts, roles, Switch user, first login with a temporary password

# Permissions (permissions.py) that belong to Documents. In the Attendance edition nobody has them, so
# every Documents button, link and sidebar item disappears without changing each page.
DOCUMENT_PERMISSIONS = frozenset({"issue", "view_issued", "add_or", "void", "templates"})
HIDDEN_PERMISSIONS = DOCUMENT_PERMISSIONS if IS_ATTENDANCE else frozenset()

# Web addresses that do not exist in the Attendance edition. Opening one goes back to the start page.
# Matched as a whole path part: "/documents" blocks /documents and /documents/history, not /documentsX.
BLOCKED_PATHS = ("/documents", "/staff", "/first-login", "/settings/documents", "/pictures") if IS_ATTENDANCE else ()

# ---- names
if IS_ATTENDANCE:
    APP_TITLE = "Barangay Queuing and Attendance Management System"
    SIDEBAR_TITLE = "Queuing & Attendance"     # the full name is too long for the sidebar
    APP_OWNER = "Soft-Tech Builders"
    SUPPORT_EMAIL = "softtech.builders26@gmail.com"
    # Its own data folder, so it never opens or upgrades the database of the Barangay System or of the
    # Barangay 4 app (those two use Barangay_Queuing_Attendance_Management).
    DATA_FOLDER = "Barangay_Attendance_Edition"
    TERMS_FILE = "terms_and_conditions_attendance.txt"
    NOTICES_FILE = "THIRD_PARTY_NOTICES_Attendance.txt"
    SINGLE_INSTANCE_NAME = "Local\\Barangay_Attendance_Edition_SingleInstance"
else:
    APP_TITLE = "Barangay System"
    SIDEBAR_TITLE = APP_TITLE
    APP_OWNER = "Jo-Tech-Developer"
    SUPPORT_EMAIL = "jotechdeveloper@gmail.com"
    DATA_FOLDER = "Barangay_Queuing_Attendance_Management"
    TERMS_FILE = "terms_and_conditions.txt"
    NOTICES_FILE = "THIRD_PARTY_NOTICES.txt"
    SINGLE_INSTANCE_NAME = "Local\\Barangay_System_SingleInstance"


def is_blocked(path: str) -> bool:
    """True if this web address belongs to a part the current edition does not have."""
    return any(path == p or path.startswith(p + "/") for p in BLOCKED_PATHS)
