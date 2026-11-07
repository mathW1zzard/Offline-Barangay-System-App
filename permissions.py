"""Staff roles: what each person may do.

The administrator builds roles from the permissions below (Setup > Staff accounts, step 9b) and gives
each person one role. The built-in Administrator role can always do everything, including managing
staff accounts and roles, and can't be changed. Every page and action in main.py names the permission
it needs, and the sidebar shows only the pages a person's role allows.

To add a permission later: add one row to PERMISSIONS, then use it in main.py. Saved roles keep working,
since unknown keys are ignored and new keys simply start unticked.
"""

# (group, key, label, what it allows), in the order the Roles page shows them
PERMISSIONS = [
    ("Front desk", "register", "Register visitors", "Client Registration: add visitors and print their tickets"),
    ("Front desk", "queue", "Manage the queue", "Queue Management: call, skip, recall, complete, no-show and clear the queue"),
    ("Documents", "issue", "Issue certificates", "Prepare and print certificates for visitors"),
    ("Documents", "view_issued", "View issued documents", "The issued documents list, reprints and its download"),
    ("Documents", "add_or", "Add OR numbers", "Record an official receipt number after a document was issued"),
    ("Documents", "void", "Void certificates", "Mark an issued document as void, with a reason"),
    ("Documents", "templates", "Edit templates and designs", "The wording, details, design and spacing of each certificate"),
    ("Records", "dashboard", "View dashboard", "Today's totals, the live queue and tickets by service"),
    ("Records", "attendance", "View attendance", "Search, print and download the visitor logbook"),
    ("Records", "audit", "View audit log", "Who did what, and when"),
    ("Setup", "services", "Manage services", "Add, edit or remove services and link them to certificates"),
    ("Setup", "settings", "Change Settings", "Barangay profile, logos, signatories and document defaults"),
    ("Setup", "maintenance", "Maintenance", "Backups, the receipt printer and purging old records"),
]
KEYS = tuple(p[1] for p in PERMISSIONS)
LABELS = {p[1]: p[2] for p in PERMISSIONS}

ADMIN_ROLE = "Administrator"
# Not a tick box: only the built-in Administrator role has it (Staff accounts and Roles).
ADMIN_ONLY = "admin_only"
# The one starter role a new or upgraded installation gets. The administrator can edit or delete it.
SECRETARY_ROLE = "Secretary"
SECRETARY = ("register", "queue", "issue", "view_issued", "add_or", "void", "templates",
             "dashboard", "attendance", "services")

# Where a person lands after logging in: the first of these pages their role allows
HOME_PAGES = (("dashboard", "/dashboard"), ("register", "/client_registration"), ("queue", "/queue_management"),
              ("issue", "/documents"), ("view_issued", "/documents/history"), ("attendance", "/attendance"),
              ("templates", "/documents/templates"), ("services", "/services"), ("audit", "/audit_logs"),
              ("settings", "/settings"), ("maintenance", "/maintenance"))
FALLBACK_HOME = "/about"           # a role with nothing ticked can still open About and log out


def parse(text) -> frozenset:
    """Saved text ("register,queue") -> the set of known permission keys. Unknown keys are dropped."""
    return frozenset(k for k in (text or "").split(",") if k in KEYS)


def dump(keys) -> str:
    """A set of keys -> the text saved in the database, in the standard order."""
    chosen = set(keys)
    return ",".join(k for k in KEYS if k in chosen)


def home_page(keys, is_admin: bool) -> str:
    if is_admin:
        return "/dashboard"
    for key, url in HOME_PAGES:
        if key in keys:
            return url
    return FALLBACK_HOME
