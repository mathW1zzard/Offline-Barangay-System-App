from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, DateTime, Date, Text, Boolean, CheckConstraint
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
    value = Column(Text, nullable=False, default="", service_default="")

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
    
class IssedDocument(Base):
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
    
MIGRATIONS = [_v1_counters_and_indexes, _v2_documents_settings_address]
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