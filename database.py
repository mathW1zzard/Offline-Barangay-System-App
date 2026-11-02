from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, DateTime, Date, Text, Boolean, CheckConstraint, LargeBinary
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
from pathlib import Path
import os, shutil, sqlite3
from sqlalchemy import event
from paths import data_path

# Connect to sqlite called tickets.db (automatic create database)
# Development: the project folder. Packaged app: %LOCALAPPDATA%\BarangayQueue (see paths.py)
engine = create_engine(f"sqlite:///{Path(data_path('barangay_system.db')).as_posix()}", connect_args={"timeout": 15})

@event.listens_for(engine, "connect")
def _sqlite_on_connect(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")              # SQLite ignores FOREIGN KEY unless asked
    cur.close()
# Base class that all our table models will inherit from
Base = declarative_base()

# Creates usable session
SessionLocal = sessionmaker(bind=engine)

# Services
class Service(Base):        
    __tablename__ = "services"
    
    id = Column(Integer, primary_key=True, index=True)
    service_name = Column(String)
    service_status = Column(String)

    # The document the "Prepare document" button opens for this service (None = no button).
    # If the template is ever deleted the link simply becomes None.
    document_template_id = Column(Integer, ForeignKey("document_templates.id", ondelete="SET NULL"), nullable=True)
    
    tickets = relationship("Ticket", back_populates="service")
    

class Ticket(Base):
    __tablename__ = "tickets"
    
    id = Column(Integer, primary_key=True, index=True)
    citizen_name = Column(String)
    lane = Column(String)
    ticket_number = Column(String)
    status = Column(String, default="waiting", index=True)
    address = Column(String, nullable=True)
    
    service_id = Column(Integer, ForeignKey("services.id"))
    
    created_at = Column(DateTime, default=datetime.now, index=True)
    called_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    
    service = relationship("Service", back_populates="tickets")
    @property
    def service_label(self) -> str:
        return self.service.service_name if self.service else "Removed service"
    
# History of all the records
class Visit(Base):
    __tablename__ = "visits"
    
    id = Column(Integer, primary_key=True, index=True)
    citizen_name = Column(String)
    lane = Column(String)
    ticket_number = Column(String)
    final_status = Column(String)
    address = Column(String, nullable=True)
    
    service_id = Column(Integer, ForeignKey("services.id"))
    
    service = relationship("Service")
    
    created_at = Column(DateTime, index=True)
    called_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    @property
    def service_label(self) -> str:
        return self.service.service_name if self.service else "Removed service"
    
class Admin(Base):
    __tablename__ = "admins"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True)
    password_hash = Column(String)
    recovery_key_hash = Column(String, nullable=True)
    
class AdminLog(Base):
    __tablename__ = "admin_logs"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.now, index=True)
    # A copy of the name, not a foreign key, so the entry stays readable if the account changes
    admin_username = Column(String)
    category = Column(String, index=True)   # Queue / Service / Settings / Security / Database
    action = Column(String)                 # short label, e.g. "Ticket Skipped"
    description = Column(String)            # the readable sentence
    
class DailyCounter(Base):
    __tablename__ = "daily_counters"
    day = Column(String, primary_key=True)        # "2026-10-11"
    lane = Column(String, primary_key=True)       # "P" or "R"
    last_number = Column(Integer, nullable=False, default=0)

# --- Documents, settings (added in migration v2) --------------------------------------------
# These tables are created by _v2_documents_settings_address below for existing installations
# and by create_all for fresh ones. Keep the two definitions identical. Indexes are created only
# in the migration (like v1), so both routes end up with the same schema.
class Setting(Base):
    """Key-value settings (barangay profile, signatories, paper size, logo versions, setup_complete).
    A missing key means "use the default in code", so new settings never need a migration."""
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(Text, nullable=False, default="", server_default="")

class DocumentTemplate(Base):
    __tablename__ = "document_templates"
    __table_args__ = (
        
        #A fee can be negative 
        CheckConstraint("default_fee_centavos >= 0", name="ck_templates_fee"),
        #An expiry must be a positive number of months
        CheckConstraint("validity_months IS NULL OR validity_months > 0", name="ck_templates_validity"),
        #The reduction of the length can't become 0
        CheckConstraint("length(trim(code_prefix)) > 0", name="ck_templates_prefix"),
        #Checking the document is active
        CheckConstraint("is_active IN (0, 1)", name="ck_templates_active"),
        CheckConstraint("print_fee IN (0, 1)", name="ck_templates_print_fee"),
        # Added in migration v4 (step 8a)
        CheckConstraint("auto_bold IN (0, 1)", name="ck_templates_auto_bold"),
        CheckConstraint("auto_indent IN (0, 1)", name="ck_templates_auto_indent"),
        CheckConstraint("signers IN ('both', 'captain', 'secretary')", name="ck_templates_signers"),
        # Added in migration v5 (step 8b): the design of the printed page and the size of the text
        CheckConstraint("design_json IS NULL OR json_valid(design_json)", name="ck_templates_design_json"),
        CheckConstraint("font_half_pt BETWEEN 18 AND 32", name="ck_templates_font_half_pt"),
        # Added in migration v6 (step 8c): spacing of the wording
        CheckConstraint("line_spacing IN (100, 115, 150, 200)", name="ck_templates_line_spacing"),
        CheckConstraint("para_gap IN ('none', 'small', 'normal')", name="ck_templates_para_gap"),
    )

    id = Column(Integer, primary_key=True)
    builtin_key = Column(String, nullable=True)   # "residency_en"... None for templates the user made
    name = Column(String, nullable=False)
    body = Column(Text, nullable=True)            # None on a built-in = use the wording in code
    code_prefix = Column(String, nullable=False)  # control numbers look like BC-2026-0001
    extras = Column(String, nullable=False, default="", server_default="")   # comma-separated keys from the fixed menu
    default_fee_centavos = Column(Integer, nullable=False, default=0, server_default="0")
    validity_months = Column(Integer, nullable=True)   # None = no expiry
    print_fee = Column(Boolean, nullable=False, default=True, server_default="1")
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    # Added in migration v4 (step 8a). The defaults keep every existing template looking as before.
    auto_bold = Column(Boolean, nullable=False, default=True, server_default="1")     # filled-in details print bold
    auto_indent = Column(Boolean, nullable=False, default=True, server_default="1")   # first line of each paragraph indented
    signers = Column(String, nullable=False, default="both", server_default="both")   # both / captain / secretary
    # Added in migration v5 (step 8b). design_json: the design read from a Word file or a picture of
    # the whole page (see documents.parse_design); NULL = a plain page, exactly as before.
    # font_half_pt: the size of the wording in half points, like Word (21 = 10.5 pt, the size so far).
    design_json = Column(Text, nullable=True)
    font_half_pt = Column(Integer, nullable=False, default=21, server_default="21")
    # Added in migration v6 (step 8c): line spacing in hundredths (115 = 1.15, like Word) and the
    # space after each paragraph.
    line_spacing = Column(Integer, nullable=False, default=115, server_default="115")
    para_gap = Column(String, nullable=False, default="small", server_default="small")
    
class IssuedDocument(Base):
    """One printed document. Everything is a plain copy so history survives edited templates,
    served tickets and purged attendance."""
    __tablename__ = "issued_documents"
    __table_args__ = (
        CheckConstraint("status IN ('issued', 'voided')", name="ck_issued_status"),
        CheckConstraint("or_status IN ('provided', 'not_available', 'no_fee')", name="ck_issued_or_status"),
        CheckConstraint("fee_centavos >= 0", name="ck_issued_fee"),
        CheckConstraint("(fee_centavos = 0) = (or_status = 'no_fee')", name="ck_issued_fee_or_status"),
        CheckConstraint("(or_status = 'provided') = (or_number IS NOT NULL)", name="ck_issued_or_number"),
        CheckConstraint("or_number IS NULL OR length(trim(or_number)) > 0", name="ck_issued_or_blank"),
        CheckConstraint("status = 'issued' OR void_reason IS NOT NULL", name="ck_issued_void_reason"),
    )
    
    id = Column(Integer, primary_key=True)
    control_number = Column(String, nullable=False)          # unique (index in the migration); never reused, even when voided
    # A template that has issued documents can never be deleted (RESTRICT): it is deactivated instead
    template_id = Column(Integer, ForeignKey("document_templates.id", ondelete="RESTRICT"), nullable=False)
    template_name = Column(String, nullable=False)           # name at the time of issue
    recipient = Column(String, nullable=False)
    address = Column(String, nullable=True)
    purpose = Column(String, nullable=True)
    issue_date = Column(Date, nullable=False)
    extras = Column(Text, nullable=True)                     # JSON: values typed for age, civil status...
    fee_centavos = Column(Integer, nullable=False, default=0, server_default="0")
    or_number = Column(String, nullable=True)
    or_status = Column(String, nullable=False, default="no_fee", server_default="no_fee")   # provided / not_available / no_fee
    final_text = Column(Text, nullable=False)                # the wording exactly as printed
    print_snapshot = Column(Text, nullable=True)             # JSON: header lines, signatories, print-fee flag at issue time
    issued_by = Column(String, nullable=False)               # plain text copy of the username
    ticket_number = Column(String, nullable=True)            # plain copies: the ticket is deleted once served
    ticket_date = Column(Date, nullable=True)
    status = Column(String, nullable=False, default="issued", server_default="issued")      # issued / voided
    void_reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    
class Picture(Base):
    """A picture kept inside the database (logos, design banners), so backups hold it too.
    A row never changes and is never deleted (see pictures.py). The unique index on sha256 is
    created in the migration, like the other indexes."""
    __tablename__ = "pictures"
    __table_args__ = (
        CheckConstraint("mime IN ('image/png', 'image/jpeg')", name="ck_pictures_mime"),
        CheckConstraint("width > 0 AND height > 0", name="ck_pictures_size"),
    )
    id = Column(Integer, primary_key=True)
    sha256 = Column(String, nullable=False)       # fingerprint: the same picture is stored once
    mime = Column(String, nullable=False)
    data = Column(LargeBinary, nullable=False)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.now)

class DocumentCounter(Base):
    __tablename__ = "document_counters"
    __table_args__ = (CheckConstraint("last_number >= 0", name="ck_doc_counters_last"),)
    prefix = Column(String, primary_key=True)
    year = Column(Integer, primary_key=True)
    last_number = Column(Integer, nullable=False, default=0, server_default="0")
    
from app_logging import log

# --- Migrations: append new functions, never edit old ones ----------------
def _v1_counters_and_indexes(con):
    con.execute("""CREATE TABLE IF NOT EXISTS daily_counters (
        day VARCHAR NOT NULL, lane VARCHAR NOT NULL,
        last_number INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, lane))""")
    # Seed from existing data so numbers already issued are never reused
    con.execute("""INSERT OR IGNORE INTO daily_counters (day, lane, last_number)
        SELECT date(created_at), lane, MAX(CAST(substr(ticket_number, 3) AS INTEGER))
        FROM (SELECT created_at, lane, ticket_number FROM tickets
              UNION ALL SELECT created_at, lane, ticket_number FROM visits)
        WHERE created_at IS NOT NULL AND ticket_number LIKE '_-%'
        GROUP BY date(created_at), lane""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_tickets_created_at ON tickets (created_at)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_tickets_status ON tickets (status)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_visits_created_at ON visits (created_at)")
    try:   # backstop for #2: the database itself refuses a duplicate number on the same day
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_tickets_day_number "
                    "ON tickets (ticket_number, date(created_at))")
    except sqlite3.IntegrityError:
        log.warning("Duplicate ticket numbers already exist; unique index skipped")

def _table_exists(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                       (name,)).fetchone() is not None

def _add_column(con, table, column, definition):
    """ALTER TABLE ... ADD COLUMN, but only if the column is not there yet. A fresh install gets
    its tables from create_all (which already has the column) BEFORE the migrations run."""
    if column not in {row[1] for row in con.execute(f"PRAGMA table_info({table})")}:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        
def _v2_documents_settings_address(con):
    """Settings, document templates, issued documents, control-number counters, the service link
    and the optional address on tickets and visits. Safe to run on an old installation and on a
    fresh one (every step is a no-op if it is already in place)."""
    con.execute("""CREATE TABLE IF NOT EXISTS settings (
        "key" VARCHAR NOT NULL PRIMARY KEY,
        value TEXT NOT NULL DEFAULT '')""")

    con.execute("""CREATE TABLE IF NOT EXISTS document_templates (
        id INTEGER NOT NULL PRIMARY KEY,
        builtin_key VARCHAR,
        name VARCHAR NOT NULL,
        body TEXT,
        code_prefix VARCHAR NOT NULL,
        extras VARCHAR NOT NULL DEFAULT '',
        default_fee_centavos INTEGER NOT NULL DEFAULT 0,
        validity_months INTEGER,
        print_fee BOOLEAN NOT NULL DEFAULT 1,
        is_active BOOLEAN NOT NULL DEFAULT 1,
        CONSTRAINT ck_templates_fee CHECK (default_fee_centavos >= 0),
        CONSTRAINT ck_templates_validity CHECK (validity_months IS NULL OR validity_months > 0),
        CONSTRAINT ck_templates_prefix CHECK (length(trim(code_prefix)) > 0),
        CONSTRAINT ck_templates_active CHECK (is_active IN (0, 1)),
        CONSTRAINT ck_templates_print_fee CHECK (print_fee IN (0, 1)))""")
    # One row per built-in key; templates the user makes have builtin_key NULL and are not limited
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_document_templates_builtin_key "
                "ON document_templates (builtin_key) WHERE builtin_key IS NOT NULL")
    
    con.execute("""CREATE TABLE IF NOT EXISTS issued_documents (
        id INTEGER NOT NULL PRIMARY KEY,
        control_number VARCHAR NOT NULL,
        template_id INTEGER NOT NULL REFERENCES document_templates (id) ON DELETE RESTRICT,
        template_name VARCHAR NOT NULL,
        recipient VARCHAR NOT NULL,
        address VARCHAR,
        purpose VARCHAR,
        issue_date DATE NOT NULL,
        extras TEXT,
        fee_centavos INTEGER NOT NULL DEFAULT 0,
        or_number VARCHAR,
        or_status VARCHAR NOT NULL DEFAULT 'no_fee',
        final_text TEXT NOT NULL,
        print_snapshot TEXT,
        issued_by VARCHAR NOT NULL,
        ticket_number VARCHAR,
        ticket_date DATE,
        status VARCHAR NOT NULL DEFAULT 'issued',
        void_reason VARCHAR,
        created_at DATETIME,
        CONSTRAINT ck_issued_status CHECK (status IN ('issued', 'voided')),
        CONSTRAINT ck_issued_or_status CHECK (or_status IN ('provided', 'not_available', 'no_fee')),
        CONSTRAINT ck_issued_fee CHECK (fee_centavos >= 0),
        CONSTRAINT ck_issued_fee_or_status CHECK ((fee_centavos = 0) = (or_status = 'no_fee')),
        CONSTRAINT ck_issued_or_number CHECK ((or_status = 'provided') = (or_number IS NOT NULL)),
        CONSTRAINT ck_issued_or_blank CHECK (or_number IS NULL OR length(trim(or_number)) > 0),
        CONSTRAINT ck_issued_void_reason CHECK (status = 'issued' OR void_reason IS NOT NULL))""")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_issued_documents_control_number "
                "ON issued_documents (control_number)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_issued_documents_issue_date ON issued_documents (issue_date)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_issued_documents_template_id ON issued_documents (template_id)")
    
    con.execute("""CREATE TABLE IF NOT EXISTS document_counters (
        prefix VARCHAR NOT NULL, year INTEGER NOT NULL,
        last_number INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (prefix, year),
        CONSTRAINT ck_doc_counters_last CHECK (last_number >= 0))""")
    
        # New columns on existing tables: all nullable, so no rows are rewritten and old code keeps working
    _add_column(con, "services", "document_template_id",
                "INTEGER REFERENCES document_templates (id) ON DELETE SET NULL")
    con.execute("CREATE INDEX IF NOT EXISTS ix_services_document_template_id "
                "ON services (document_template_id)")
    _add_column(con, "tickets", "address", "VARCHAR")
    _add_column(con, "visits", "address", "VARCHAR")
    
        # The four built-in documents. No wording is stored: a NULL body means "use the built-in
    # wording from the code", so correcting a default later needs no migration. Fees start at 0
    # (No fee): each barangay sets its own amounts under Templates.
    con.executemany(
        """INSERT OR IGNORE INTO document_templates
           (builtin_key, name, body, code_prefix, extras, default_fee_centavos, validity_months, print_fee, is_active)
           VALUES (?, ?, NULL, ?, '', 0, NULL, 1, 1)""",
        [("residency_en", "Certificate of Residency", "CR"),
         ("clearance_en", "Barangay Clearance", "BC"),
         ("indigency_en", "Certificate of Indigency", "CI"),
         ("blank_en", "Blank Document", "DOC")])
    
        # An installation that already has an administrator came from the Barangay 4 edition, so it is
    # already set up: carry its old hard-coded values into settings and skip the first-run wizard.
    # A fresh install has no administrator yet, so nothing is seeded and the wizard runs after sign-in.
    # Signatory NAMES are deliberately not seeded: the old printout never carried names, and a stale
    # name on a certificate is worse than a blank one. Only the titles are kept.
    if _table_exists(con, "admins") and con.execute("SELECT 1 FROM admins LIMIT 1").fetchone():
        con.executemany(
            'INSERT OR IGNORE INTO settings ("key", value) VALUES (?, ?)',
            [("barangay_name", "Barangay 4"),
             ("municipality", "San Mateo"),
             ("province", "Isabela"),
             ("header_line_1", "Republic of the Philippines"),
             ("signatory_captain_title", "Barangay Captain"),
             ("signatory_secretary_title", "Barangay Secretary"),
             ("setup_complete", "1")])

def _v3_legacy_logo_flag(con):
    """Upgraded Barangay 4 installations still have their three logos bundled as files. Mark them
    so the app copies those files into the new logo slots once (see branding.seed_legacy_logos).
    A fresh install has no setup_complete yet, so it never gets the flag."""
    con.execute("""INSERT OR IGNORE INTO settings ("key", value)
                   SELECT 'legacy_logos_pending', '1'
                   WHERE EXISTS (SELECT 1 FROM settings WHERE "key" = 'setup_complete' AND value = '1')""")

def _v4_wording_options(con):
    """Step 8a: three template options, and the control number moves into the wording.
    - auto_bold, auto_indent: ON, so existing templates look exactly as before.
    - signers: 'both', as before.
    - Saved wordings get "Control No.: {Control number}" as their first paragraph, so no template
      silently loses its printed number (until now the print page added it on its own). Built-in
      wordings that were never changed (body NULL) get the same line from the code instead.
      A wording that is already at the length limit is left alone: the Templates tab then shows
      that it prints no number. Issued documents are NOT touched: their reprints keep the old
      fixed number line (their snapshot has no "number_in_text")."""
    _add_column(con, "document_templates", "auto_bold",
                "BOOLEAN NOT NULL DEFAULT 1 CONSTRAINT ck_templates_auto_bold CHECK (auto_bold IN (0, 1))")
    _add_column(con, "document_templates", "auto_indent",
                "BOOLEAN NOT NULL DEFAULT 1 CONSTRAINT ck_templates_auto_indent CHECK (auto_indent IN (0, 1))")
    _add_column(con, "document_templates", "signers",
                "VARCHAR NOT NULL DEFAULT 'both' "
                "CONSTRAINT ck_templates_signers CHECK (signers IN ('both', 'captain', 'secretary'))")
    line = "Control No.: {Control number}\n\n"
    con.execute("""UPDATE document_templates SET body = ? || body
                   WHERE body IS NOT NULL
                     AND instr(lower(body), '{control number}') = 0
                     AND length(body) + ? <= 5000""", (line, len(line)))

def _png_size(data: bytes):
    """Width and height from a PNG file's header (the saved logos are always PNG)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24:
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")

def _v5_pictures_and_design(con):
    """Step 8b: pictures inside the database, a design and a text size for each document template.
    - pictures: logos and design pictures, so a backup (one copy of the database) holds them.
    - Each template gets design_json (NULL = a plain page) and font_half_pt (21 = 10.5 pt, the size
      used so far), so every template prints exactly as before until its design is changed.
    - The logos saved as files next to the database are copied in, and Settings points at them.
      The files are left where they are (nothing is deleted); the app simply stops reading them."""
    con.execute("""CREATE TABLE IF NOT EXISTS pictures (
        id INTEGER NOT NULL PRIMARY KEY,
        sha256 VARCHAR NOT NULL,
        mime VARCHAR NOT NULL,
        data BLOB NOT NULL,
        width INTEGER NOT NULL,
        height INTEGER NOT NULL,
        created_at DATETIME,
        CONSTRAINT ck_pictures_mime CHECK (mime IN ('image/png', 'image/jpeg')),
        CONSTRAINT ck_pictures_size CHECK (width > 0 AND height > 0))""")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_pictures_sha256 ON pictures (sha256)")

    _add_column(con, "document_templates", "design_json",
                "TEXT CONSTRAINT ck_templates_design_json CHECK (design_json IS NULL OR json_valid(design_json))")
    _add_column(con, "document_templates", "font_half_pt",
                "INTEGER NOT NULL DEFAULT 21 CONSTRAINT ck_templates_font_half_pt CHECK (font_half_pt BETWEEN 18 AND 32)")

    # The logos: from the files next to the database into the pictures table
    import hashlib
    for slot in ("left", "right", "third"):
        in_use = con.execute('SELECT value FROM settings WHERE "key" = ?', (f"logo_{slot}_v",)).fetchone()
        done = con.execute('SELECT value FROM settings WHERE "key" = ?', (f"logo_{slot}_pic",)).fetchone()
        if not in_use or not in_use[0] or (done and done[0]):
            continue                                   # no logo in this slot, or already copied
        try:
            data = Path(data_path(f"logo_{slot}.png")).read_bytes()
        except OSError:
            log.warning("Logo file for the %s slot is missing; that slot stays empty", slot)
            continue
        size = _png_size(data)
        if size is None:
            log.warning("Logo file for the %s slot is not a PNG; that slot stays empty", slot)
            continue
        digest = hashlib.sha256(data).hexdigest()
        con.execute("""INSERT OR IGNORE INTO pictures (sha256, mime, data, width, height, created_at)
                       VALUES (?, 'image/png', ?, ?, ?, ?)""",
                    (digest, data, size[0], size[1], datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")))
        picture_id = con.execute("SELECT id FROM pictures WHERE sha256 = ?", (digest,)).fetchone()[0]
        con.execute('INSERT OR REPLACE INTO settings ("key", value) VALUES (?, ?)', (f"logo_{slot}_pic", str(picture_id)))

def _v6_spacing(con):
    """Step 8c: line spacing and the space after each paragraph, per template. Every template gets
    1.15 and a small space (close to Word). Issued documents are not touched: they keep the spacing
    they were printed with (see documents.snapshot_look)."""
    _add_column(con, "document_templates", "line_spacing",
                "INTEGER NOT NULL DEFAULT 115 "
                "CONSTRAINT ck_templates_line_spacing CHECK (line_spacing IN (100, 115, 150, 200))")
    _add_column(con, "document_templates", "para_gap",
                "VARCHAR NOT NULL DEFAULT 'small' "
                "CONSTRAINT ck_templates_para_gap CHECK (para_gap IN ('none', 'small', 'normal'))")

MIGRATIONS = [_v1_counters_and_indexes, _v2_documents_settings_address, _v3_legacy_logo_flag, _v4_wording_options,
              _v5_pictures_and_design, _v6_spacing]
LATEST = len(MIGRATIONS)

def _has_table(db_file, name):
    con = sqlite3.connect(db_file)
    try:
        return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                           (name,)).fetchone() is not None
    finally:
        con.close()

def _apply_migrations(db_file):
    con = sqlite3.connect(db_file, isolation_level=None, timeout=15)   # manual transactions
    try:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        for target, step in enumerate(MIGRATIONS, start=1):
            if version >= target:
                continue
            con.execute("BEGIN IMMEDIATE")
            try:
                step(con)
                con.execute(f"PRAGMA user_version = {target}")   # committed with the step
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
    finally:
        con.close()

def run_migrations():
    db_file = data_path("barangay_system.db")
    existing = os.path.exists(db_file) and _has_table(db_file, "tickets")
    if existing:
        con = sqlite3.connect(db_file)
        behind = con.execute("PRAGMA user_version").fetchone()[0] < LATEST
        con.close()
        if behind:   # safety copy before touching a real installation
            shutil.copy2(db_file, f"{db_file}.before_v{LATEST}.bak")
        _apply_migrations(db_file)
    Base.metadata.create_all(bind=engine)          # brand-new tables for fresh installs
    if not existing:
        _apply_migrations(db_file)                 # stamps the version on a fresh DB

run_migrations()