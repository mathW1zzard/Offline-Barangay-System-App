from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi import Request 
from fastapi import Form
from fastapi import Depends
from fastapi import File, UploadFile
from fastapi.responses import RedirectResponse, Response, FileResponse, JSONResponse
from app_logging import setup_logging, log
setup_logging()
from database import SessionLocal, Ticket, Service, Visit, Admin, AdminLog, Role, engine, DocumentTemplate, IssuedDocument
from database import LATEST as DB_LATEST
from paths import resource_path, data_path, DATA_DIR
import branding
import permissions as perms
import doc_templates
import documents
import pictures
import docx_design
from datetime import datetime, date, time, timedelta
from starlette.middleware.sessions import SessionMiddleware
from enum import Enum
import threading
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi import WebSocket, WebSocketDisconnect
from escpos.printer import Dummy
import json
import sys
import textwrap
import unicodedata
from collections import Counter
from sqlalchemy import desc, func, text
from pathlib import Path
import bcrypt
import csv
import io
import sqlite3
import asyncio
import math
import re
import os
import platform
from time import monotonic          
from urllib.parse import urlparse, urlencode
from contextlib import asynccontextmanager
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

# For authentication(recovery key)
import secrets
import string

main_loop: asyncio.AbstractEventLoop | None = None


@asynccontextmanager
async def lifespan(_app):
    global main_loop
    main_loop = asyncio.get_running_loop()        # remember uvicorn's loop for worker threads
    yield

app = FastAPI(lifespan=lifespan)

def load_secret_key() -> str:
    key_file = Path(data_path("session_secret.key"))
    try:
        key = key_file.read_text(encoding="utf-8").strip()
        if len(key) >= 64:
            return key
        log.warning("Session secret was damaged; generating a new one.")
    except OSError:
        pass                                   # missing file: first run

    key = secrets.token_urlsafe(64)
    tmp = key_file.with_suffix(".tmp")
    tmp.write_text(key, encoding="utf-8")
    os.replace(tmp, key_file)                  # atomic: never leaves a half-written key
    return key

app.add_middleware(SessionMiddleware,
    secret_key=load_secret_key(),
    session_cookie="bqs_session", 
    same_site="lax",
    max_age=8 * 60 * 60,
)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

@app.middleware("http")
async def block_cross_origin_posts(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host", ""):
            return Response("Cross-origin request blocked", status_code=403)
    return await call_next(request)

def branding_context(request: Request) -> dict:
    """Runs for every page: gives each template the barangay name, logos and signatories from Settings."""
    b = branding.get_branding()
    return {"branding": b, "barangay_name": b["full_name"]}

templates = Jinja2Templates(directory=resource_path("templates"), context_processors=[branding_context])

APP_TITLE = "Barangay System"                 # the name people see; change it here only
APP_VERSION = "2.0.0"                         # keep the same as MyAppVersion in installer.iss
APP_RELEASED = "November 2026"               # when this version came out (About page)
APP_OWNER = "Jo-Tech-Developer"               # shown in every footer and on the About page
SUPPORT_EMAIL = "jotechdeveloper@gmail.com"   # where barangays send questions and problem reports
COPYRIGHT_YEAR = 2026
templates.env.globals.update(app_title=APP_TITLE, app_version=APP_VERSION, app_owner=APP_OWNER,
                             support_email=SUPPORT_EMAIL, copyright_year=COPYRIGHT_YEAR)

# APPEARANCE: light / dark mode. Saved in a file next to the database, because the desktop window
# starts in private mode on a new port each launch, so the browser itself cannot remember it.
UI_PREFS_FILE = Path(data_path("ui_prefs.json"))
THEMES = ("system", "light", "dark")           # system = follow the Windows setting
DISPLAY_THEMES = ("light", "dark")             # the visitor display screen has its own setting
_ui_prefs_lock = threading.Lock()


def load_ui_prefs() -> dict:
    try:
        data = json.loads(UI_PREFS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}                              # missing or damaged file: use the defaults
    if not isinstance(data, dict):
        data = {}
    theme, display = data.get("theme"), data.get("display_theme")
    return {"theme": theme if theme in THEMES else "system",
            "display_theme": display if display in DISPLAY_THEMES else "light"}


def save_ui_pref(key: str, value: str) -> None:
    with _ui_prefs_lock:                       # two quick clicks must not lose each other's change
        prefs = load_ui_prefs()
        prefs[key] = value
        tmp = UI_PREFS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(prefs), encoding="utf-8")
        os.replace(tmp, UI_PREFS_FILE)         # atomic, like the printer settings


templates.env.globals["ui_theme"] = lambda: load_ui_prefs()["theme"]
templates.env.globals["display_theme"] = lambda: load_ui_prefs()["display_theme"]
PAGE_SIZE = 15

def render_registration(request: Request, error: str | None = None, form: dict | None = None, status_code: int = 200):
    with SessionLocal() as db:
        today_start = datetime.combine(date.today(), time.min)
        tickets = db.query(Ticket).filter(Ticket.created_at >= today_start).order_by(desc(Ticket.created_at)).all()
        services = db.query(Service).filter(Service.service_status == "Active").all()
        return templates.TemplateResponse(request, "client_registration.html", {
            "tickets": tickets, "services": services, "total_today": len(tickets),
            "queue_info": queue_snapshot(), "error": error, "form": form,
            "auto_print": AUTO_PRINT_ON_REGISTER,
        }, status_code=status_code)


def current_priority_streak(db) -> int:
    """Consecutive Priority calls at the end of today's call order (0, 1 or 2).
    Derived from called_at, so a restart cannot reset it."""
    start = datetime.combine(date.today(), time.min)
    calls = (db.query(Ticket.lane, Ticket.called_at).filter(Ticket.called_at >= start).all()
             + db.query(Visit.lane, Visit.called_at).filter(Visit.called_at >= start).all())
    streak = 0
    for lane, _ in sorted(calls, key=lambda r: r[1], reverse=True)[:2]:
        if lane != "P":
            break
        streak += 1
    return streak

THROTTLE_MAX_FAILURES, THROTTLE_WINDOW_SECONDS = 5, 60

# ADMIN LOGS
LOG_CATEGORIES = ["Queue", "Service", "Settings", "Security", "Database", "Documents", "Staff"]
NOT_SIGNED_IN = "(not signed in)" # who gets blamed for failed logins

# SESSION TRACKING (so the audit log can record a session that ended by closing the app)
active_admin_id = None
SESSION_EVENTS = ("Login", "Logout", "Session Closed", "Session Interrupted")
SESSION_SECRET = load_secret_key() 

# DAILY ROLLOVER (tickets left over from a previous day)
last_rollover_date = None            # the day the cleanup last ran in this session
last_backup_attempt = None
rollover_retry_at = 0.0
rollover_failed = False
ROLLOVER_RETRY_SECONDS = 300
rollover_lock = threading.Lock()

# MAINTENANCE
BACKUP_DIR_DEFAULT = Path(data_path("backups"))
PURGE_MIN_AGE_DAYS = 30 # safety: records newer than this can never be purged

# AUTOMATIC BACKUPS
AUTO_BACKUP_KEEP_DAYS = 14   # keep the newest automatic backup of each of the last 14 days that have one
AUTO_BACKUP_NAME = re.compile(r"^auto_(\d{8})_\d{6}(?:_\d+)?\.db$")   # the ONLY file names cleanup may ever delete
EXTRA_BACKUP_SETTING = Path(data_path("backup_folder.txt"))   # one line: full path of a second backup folder

def flash(request: Request, ok: bool, text_: str):
    """One-time message shown on the next page load (stored in the session cookie)."""
    request.session["flash"] = {"ok": ok, "text": text_}
    
def log_action(category: str, action: str, description: str = "", request: Request = None, username: str = None):
    """Write one row to the admin audit log. Call it AFTER the action succeeded.

    Who did it: pass `request` (the signed-in admin is looked up from the session) or pass
    `username` directly (login, recovery, background events). With neither, it is logged as "system".
    It never raises: a logging problem must not stop the real action (like calling a ticket).
    """
    try:
        db = SessionLocal()
        try:
            if username is None and request is not None:
                admin_id = request.session.get("admin_id")
                admin = db.query(Admin).filter(Admin.id == admin_id).first() if admin_id else None
                username = admin.username if admin else None
            db.add(AdminLog(
                admin_username=username or "system",
                category=category,
                action=action,
                description=description,
                created_at=datetime.now(),
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        log.exception("Audit log failed")

def log_action_throttled(request: Request, category: str, action: str, description: str, seconds: int = 60, about: str = ""):
    """Log at most once per `seconds` per session for the same action (refreshing a page is not a new event).
    `about` keeps separate limits for separate things, e.g. each refused page gets its own entry."""
    now = datetime.now().timestamp()
    key = f"last_log_{action}{about}"
    if now - request.session.get(key, 0) < seconds:
        return
    request.session[key] = now
    log_action(category, action, description, request)
    
def log_app_start():
    """Run once when the program starts (called from run_app.py).

    1. If an admin's last session event is a "Login" with nothing after it, the previous run ended
       without a logout and without a normal window close (crash, power cut, killed in Task Manager).
       Record that now. The real end time is unknown, so the entry says it was detected at startup.
    2. Record that the application started.
    """
    try:
        db = SessionLocal()
        try:
            names = [r[0] for r in db.query(AdminLog.admin_username).filter(AdminLog.action == "Login").distinct().all()]
            unfinished = []
            for name in names:
                last = db.query(AdminLog).filter(AdminLog.admin_username == name, AdminLog.category == "Security", AdminLog.action.in_(SESSION_EVENTS)).order_by(AdminLog.id.desc()).first()
                
                if last and last.action == "Login":
                    unfinished.append((name, last.created_at))
        finally:
            db.close()            

        for name, since in unfinished:
            began = since.strftime("%m/%d/%Y %I:%M %p") if since else "an earlier time"
            log_action("Security", "Session Interrupted",
                       f"Session that began {began} was never closed (power loss, crash or forced shutdown). "
                       f"Detected at startup, so the real end time is unknown.", username=name)
        log_action("Security", "Application Started", "Application started", username="system")
        
    except Exception:
        log.exception("Startup audit check failed")
  
def log_app_closed():
    """Run after the app window has closed (called from run_app.py). Logs it only if someone was still signed in."""
    if active_admin_id is None:
        return
    try:
        db = SessionLocal()
        try:
            admin = db.query(Admin).filter(Admin.id == active_admin_id).first()
            name = admin.username if admin else None
        finally:
            db.close()
        if name:
            log_action("Security", "Session Closed", "Application was closed without signing out", username=name)
    except Exception as e:
        log.exception("Closed audit log failed")
          
def parse_date(value: str):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None

def archive_stale_tickets() -> int:
    """Tickets registered on an earlier day can never be served today (numbers restart every day).

    Save each one in attendance history as a no-show, on the day it was registered, and remove it
    from the live queue. All in one transaction. Returns how many tickets were archived.
    """

    today_start = datetime.combine(date.today(), time.min)
    with SessionLocal() as db:
        stale = db.query(Ticket).filter(
            Ticket.created_at < today_start,
            Ticket.status.in_(["waiting", "called", "skipped"]),
        ).all()
        if not stale:
            return 0

        counts = Counter(t.status for t in stale)
        days = sorted({t.created_at.date() for t in stale})
        total = len(stale)
        for t in stale:
            db.add(Visit(
                citizen_name=t.citizen_name, lane=t.lane, ticket_number=t.ticket_number,
                final_status="no_show", service_id=t.service_id, address=t.address,
                created_at=t.created_at,      # stays on the day it was registered
                called_at=t.called_at,
                completed_at=None,            # never served, so no time out ("--" on the pages)
            ))
            db.delete(t)
        db.commit()

    log_action("Queue", "Old Tickets Archived",
               f"{total} ticket(s) left over from {range_label(days[0], days[-1])} were saved as no-show "
               f"({counts['waiting']} waiting, {counts['called']} called, {counts['skipped']} skipped)",
               username="system")
    return total

def create_backup_file(target_dir: Path, prefix: str) -> Path:
    """Copy the live database into target_dir with SQLite's backup API, then check the copy.

    It is written under a temporary name and renamed only when the check passes, so a half-written
    file (power cut, unplugged drive) can never be mistaken for a real backup. Raises on any problem.
    """
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    target, n = target_dir / f"{prefix}{stamp}.db", 1
    while target.exists():
        target = target_dir / f"{prefix}{stamp}_{n}.db"
        n += 1
    
    temp = target.with_suffix(".tmp")
    try:
        source = sqlite3.connect(data_path("barangay_system.db"))
        try:
            dest = sqlite3.connect(str(temp))
            try:
                source.backup(dest)
                verdict = dest.execute("PRAGMA quick_check").fetchone()[0]
            finally:
                dest.close()
        finally:
            source.close()
        if verdict != "ok":
            raise RuntimeError(f"the copy failed its integrity check ({verdict})")
        temp.replace(target)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return target

def _today_start_str() -> str:
    return datetime.combine(date.today(), time.min).strftime("%Y-%m-%d %H:%M:%S.%f")  # same format SQLAlchemy stores

_CLAIM = text("""UPDATE tickets SET status='called', called_at=COALESCE(called_at, :now)
                 WHERE id=:id AND status=:from_status
                   AND NOT EXISTS (SELECT 1 FROM tickets
                                   WHERE status='called' AND created_at >= :today_start)""")

def get_extra_backup_dir():
    """The optional second backup folder (an external drive, say), or None."""
    try:
        value = EXTRA_BACKUP_SETTING.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(value) if value else None


def list_auto_backups(folder: Path):
    """Our own automatic backups in a folder, newest first."""
    try:
        files = [f for f in folder.iterdir() if f.is_file() and AUTO_BACKUP_NAME.match(f.name)]
    except OSError:
        return []
    return sorted(files, key=lambda f: f.name, reverse=True)   # the names start with the date and time


def prune_auto_backups(folder: Path):
    """Keep the newest automatic backup of each of the last AUTO_BACKUP_KEEP_DAYS days that have one.

    Only files named exactly like our automatic backups are touched: manual backups and anything
    else in the folder are never deleted. A day without a backup is skipped, not counted, so a
    long break from using the app never wipes out the only backups.
    """
    try:
        kept_days = set()
        for f in list_auto_backups(folder):   # newest first
            day = AUTO_BACKUP_NAME.match(f.name).group(1)
            if day not in kept_days and len(kept_days) < AUTO_BACKUP_KEEP_DAYS:
                kept_days.add(day)            # the newest backup of a recent day: keep
            else:
                f.unlink(missing_ok=True)     # an older backup of the same day, or a day past the limit
        for leftover in folder.glob("auto_*.tmp"):
            leftover.unlink(missing_ok=True)  # a backup that was interrupted half way
    except OSError:
        log.exception("Cleaning up old backups failed")


def auto_backup(reason: str) -> bool:
    """Make an automatic backup, plus a copy in the second folder if one is set. Never raises.

    Returns True if at least one copy was saved. Everything is recorded in the audit log.
    """
    saved, problems = [], []

    try:
        BACKUP_DIR_DEFAULT.mkdir(parents=True, exist_ok=True)
        saved.append(str(create_backup_file(BACKUP_DIR_DEFAULT, "auto_")))
        prune_auto_backups(BACKUP_DIR_DEFAULT)
    except Exception as e:
        log.exception("Automatic backup failed")
        problems.append(f"backup failed: {e}")

    extra = get_extra_backup_dir()
    if extra:
        try:
            if not extra.is_dir():
                raise FileNotFoundError(f"folder not found: {extra}")
            saved.append(str(create_backup_file(extra, "auto_")))
            prune_auto_backups(extra)
        except Exception as e:
            log.exception("Automatic backup copy failed")
            problems.append(f"copy to {extra} failed: {e}")

    if saved:
        log_action("Database", "Auto Backup", f"{reason}: saved " + "; ".join(saved), username="system")
    if problems:
        log_action("Database", "Backup Failed", f"{reason}: " + "; ".join(problems), username="system")
    return bool(saved)

def ensure_daily_rollover():
    """Runs the cleanup above once per calendar day, whenever the first page of that day is opened.

    This also covers a computer that stays on past midnight. After the first run of the day it costs
    one date comparison.
    """
    global last_rollover_date, last_backup_attempt, rollover_retry_at, rollover_failed
    today = date.today()
    if last_rollover_date == today or monotonic() < rollover_retry_at:
        return
    with rollover_lock:
        if last_rollover_date == today or monotonic() < rollover_retry_at:
            return
        if last_backup_attempt != today:
          last_backup_attempt = today
          auto_backup("Daily Backup")  
        try:
            archive_stale_tickets()
        except Exception:
            log.exception("Daily rollover failed")
            rollover_failed = True
            rollover_retry_at = monotonic() + ROLLOVER_RETRY_SECONDS
            return
        rollover_failed = False
        last_rollover_date = today
        
templates.env.globals["rollover_failed"] = lambda: rollover_failed

def active_queue_today():
    """Tickets registered today that are still in the queue."""
    db = SessionLocal()
    
    try:
        today_start = datetime.combine(date.today(), datetime.min.time())
        return db.query(Ticket).filter(
            Ticket.created_at >= today_start,
            Ticket.status.in_(["waiting", "called", "skipped"]),
        ).count()
    finally:
        db.close()
        
templates.env.globals["active_queue_today"] = active_queue_today

class StatusEnum(str, Enum):
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    
app.mount("/static", StaticFiles(directory=resource_path("static")), name="static")

# WEBSOCKET
class ConnectionManager:
    def __init__(self):
        self.active: set[WebSocket] = set()
        
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active.add(websocket)
        
    def disconnect(self, websocket: WebSocket):
            self.active.discard(websocket)
            
    async def broadcast(self, message: str):
        dead = []
        for ws in list(self.active):              # iterate a copy
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)                   # one bad socket no longer stops the rest
        for ws in dead:
            self.active.discard(ws)
            
manager = ConnectionManager()

@app.websocket("/ws/display")
async def websocket_display(websocket: WebSocket):
    origin = websocket.headers.get("origin")
    if origin and urlparse(origin).netloc != websocket.headers.get("host", ""):
        await websocket.close(code=1008)          # refuse pages from other origins
        return
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)             # also runs on unexpected errors, so no leak


def notify_display_update():
    """Safe to call from the normal (threaded) routes."""
    if main_loop is not None and main_loop.is_running():
        asyncio.run_coroutine_threadsafe(manager.broadcast("update"), main_loop)
    
# ANNOUNCEMENT AUDIO
def build_announcement_clips(ticket_number: str):
    """Convert a ticket number such as P-047 into a list of reusable audio clips."""

    m = TICKET_RE.match(ticket_number or "")
    if not m:
        return []
    letter, digits = m.groups()
    return ["ticket_number", f"letters/{letter}", *[f"digits/{d}" for d in digits], "proceed_to_counter"]

       
# True  = the slip prints by itself when a ticket is registered (the confirmation page then
#         offers "Print again").
# False = nothing prints until "Print ticket slip" is pressed on the confirmation page.


# PRINTER
AUTO_PRINT_ON_REGISTER = True
PRINTER_SETTINGS_FILE = Path(data_path("printer_settings.json"))
PAPER_COLUMNS = {"58": 32, "80": 48}           # characters per line on 58 mm and 80 mm paper
VIRTUAL_PRINTER_WORDS = ("pdf", "xps", "onenote", "fax", "document writer")
CHECK_PRINTER_STATUS = True                    # set False if Windows wrongly reports your printer as offline
HAS_CUTTER = False            # tear-off printer; set True only if yours has a blade
FEED_LINES = 4                # blank lines fed after the slip so it clears the tear bar
CANCEL_CHINESE_MODE = True    # some Xprinter units start in Chinese (GBK) mode, which can garble Ñ and é

# On a non-Windows computer (or with BQS_DUMMY_PRINTER=1) slips are written to a file instead of printed.
USE_DUMMY_PRINTER = sys.platform != "win32" or bool(os.environ.get("BQS_DUMMY_PRINTER"))

# Windows printer status bits (winspool.h) and what to tell the clerk
_STATUS_PROBLEMS = {
    0x1: "is paused", 0x2: "reports an error", 0x8: "has a paper jam", 0x10: "is out of paper",
    0x40: "has a paper problem", 0x80: "is offline", 0x1000: "is not available",
    0x400000: "has its cover open",
}
_ATTRIBUTE_WORK_OFFLINE = 0x400


class PrinterError(Exception):
    """A printing problem, worded so a clerk can understand it."""


def load_printer_settings() -> dict:
    try:
        data = json.loads(PRINTER_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    paper = str(data.get("paper_mm", "58"))
    return {"printer_name": str(data.get("printer_name", "")).strip(),
            "paper_mm": paper if paper in PAPER_COLUMNS else "58"}


def save_printer_settings(printer_name: str, paper_mm: str) -> None:
    tmp = PRINTER_SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"printer_name": printer_name, "paper_mm": paper_mm}), encoding="utf-8")
    os.replace(tmp, PRINTER_SETTINGS_FILE)       # atomic, like the session key


def list_windows_printers() -> list[str]:
    """Printers installed in Windows, without the virtual ones (PDF, XPS, Fax...)."""
    if sys.platform != "win32":
        return []
    try:
        import win32print
        found = win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)
        names = [p[2] for p in found]            # each entry: (flags, description, name, comment)
    except Exception:
        log.exception("Could not list the Windows printers")
        return []
    return sorted(n for n in names if not any(w in n.lower() for w in VIRTUAL_PRINTER_WORDS))


def send_to_windows_printer(printer_name: str, data: bytes) -> None:
    """Hand raw ESC/POS bytes to the Windows print spooler. Raises PrinterError with a plain message."""
    import win32print
    try:
        handle = win32print.OpenPrinter(printer_name)
    except Exception as e:
        raise PrinterError(f'Windows cannot open the printer "{printer_name}". '
                           f"Is it installed and switched on? ({e})") from e
    try:
        if CHECK_PRINTER_STATUS:
            info = win32print.GetPrinter(handle, 2)
            problems = [msg for bit, msg in _STATUS_PROBLEMS.items() if info.get("Status", 0) & bit]
            if info.get("Attributes", 0) & _ATTRIBUTE_WORK_OFFLINE:
                problems.append('is set to "Use Printer Offline"')
            if problems:
                # A job sent now would sit in the queue and print later, maybe twice if the clerk retries.
                raise PrinterError(f'The printer "{printer_name}" ' + " and ".join(problems) + ".")
        win32print.StartDocPrinter(handle, 1, ("Queue slip", None, "RAW"))
        try:
            win32print.StartPagePrinter(handle)
            win32print.WritePrinter(handle, data)
            win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
    finally:
        win32print.ClosePrinter(handle)


def deliver_to_printer(data: bytes, settings: dict) -> None:
    if USE_DUMMY_PRINTER:
        with open(data_path("last_ticket_printed.txt"), "wb") as f:      # contains client names: test mode only
            f.write(data)
        log.info("Dummy printer: slip saved to last_ticket_printed.txt")
        return
    if not settings["printer_name"]:
        raise PrinterError("No printer is selected. Choose one under Maintenance > Receipt printer.")
    send_to_windows_printer(settings["printer_name"], data)


def slip_text(value: str) -> str:
    """Keeps letters the printer's default code page (CP437: Ñ ñ é í ó ú...) can print.
    Anything else is reduced to plain letters, so it never prints as garbage."""
    def printable(ch: str) -> str:
        try:
            ch.encode("cp437")
            return ch
        except UnicodeEncodeError:
            return unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
    return "".join(printable(c) for c in unicodedata.normalize("NFC", value or ""))

SIZE_NORMAL = (1, 1)
SIZE_HEADER = (1, 2)    # barangay name: taller, same width
SIZE_TICKET = (5, 5)    # "P-008" = 25 columns; even "P-1000" = 30 still fits
SIZE_LANE   = (2, 2)    # "PRIORITY LANE" = 26 columns
SIZE_BODY   = (1, 2)    # client, service, date, footer: taller, same width, so names wrap no more than before

def _style(p, align="left", bold=False, size=SIZE_NORMAL):
    """Set alignment, bold and character size. Size stays until changed, so every block sets it."""
    p.set(align=align, bold=bold)
    w, h = size
    p._raw(b"\x1d!" + bytes([((w - 1) << 4) | (h - 1)]))     # GS ! n: width in the high bits, height in the low
    
    
def build_slip(ticket_number: str, citizen_name: str, service_name: str, lane: str,
               created_at: datetime, columns: int) -> bytes:
    p = Dummy()                                   # only builds the ESC/POS bytes; nothing is printed here
    p._raw(b"\x1b@")                              # reset the printer to a known state
    if CANCEL_CHINESE_MODE:
        p._raw(b"\x1c\x2e")                       # FS . = cancel Chinese character mode
    rule = "-" * columns + "\n"

    def fill(value: str, size: tuple, label: str = "") -> str:
        # Larger text means fewer characters per line, so wrap at columns / width.
        width = max(8, columns // size[0])
        return textwrap.fill(label + slip_text(value), width=width, subsequent_indent=" " * len(label)) + "\n"

    _style(p, "center", True, SIZE_HEADER)
    p.text(fill(branding.get_branding()["full_name"].upper(), SIZE_HEADER))
    _style(p, "center", False, SIZE_NORMAL)
    p.text("Official Queue Slip\n")
    p.text(rule)

    _style(p, "center", True, SIZE_TICKET)
    p.text(f"{ticket_number}\n")
    _style(p, "center", True, SIZE_LANE)
    p.text(("PRIORITY LANE" if lane == "P" else "REGULAR LANE") + "\n")
    _style(p, "center", False, SIZE_NORMAL)
    p.text(rule)

    _style(p, "left", False, SIZE_BODY)
    p.text(fill(citizen_name, SIZE_BODY, "Client : "))
    p.text(fill(service_name, SIZE_BODY, "Service: "))
    p.text(fill(created_at.strftime("%b %d, %Y %I:%M %p"), SIZE_BODY, "Date   : "))
    _style(p, "center", False, SIZE_NORMAL)
    p.text(rule)

    _style(p, "center", False, SIZE_BODY)
    p.text("Please wait for your number\nto be called. Thank you!\n")

    _style(p, "center", False, SIZE_NORMAL)        # back to 1x so the feed below is a normal length
    p.text("\n" * FEED_LINES)
    if HAS_CUTTER:
        p.cut()
    return p.output


def build_test_slip(columns: int) -> bytes:
    p = Dummy()
    p._raw(b"\x1b@")
    
    if CANCEL_CHINESE_MODE:
        p._raw(b"\x1c\x2e")                       # FS . = cancel Chinese character mode
    
    p.set(align="center", bold=True, width=2, height=2)
    p.text("TEST\n")
    p.set(align="center", bold=False, width=1, height=1)
    p.text("The printer is working\n")
    p.text("-" * columns + "\n")
    p.set(align="left", bold=False, width=1, height=1)
    p.text(("1234567890" * 6)[:columns] + "\n")  # must fit on ONE row; if it wraps, the paper width setting is too big
    p.text(slip_text("Accents: \u00d1 \u00f1 \u00e9 \u00ed \u00f3 \u00fa") + "\n")
    p.text(f"Paper setting: {columns} characters per line\n")
    
    p.text("\n" * FEED_LINES)
    if HAS_CUTTER:
        p.cut()
    p.cut()
    return p.output


def print_ticket(ticket_number: str, citizen_name: str, service_name: str, lane: str, created_at: datetime) -> bool:
    """Send the queue slip to the printer. Returns True if it was handed over, False if it failed."""
    try:
        settings = load_printer_settings()
        data = build_slip(ticket_number, citizen_name, service_name, lane, created_at,
                          PAPER_COLUMNS[settings["paper_mm"]])
        deliver_to_printer(data, settings)
        return True
    except Exception as e:
        log.exception("Ticket printing failed for %s", ticket_number)
        log_action("Settings", "Print Error", f"Ticket slip {ticket_number} failed to print: {e}", username="system")
        return False

_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
TICKET_RE = re.compile(r"^([PR])-(\d{1,4})$")

def clean_text(value: str, max_len: int) -> str:
    """Strips control characters (they would reach the ESC/POS printer), collapses whitespace, caps length."""
    return " ".join(_CTRL.sub(" ", value or "").split())[:max_len]

def parse_optional_int(value: str):
    try:
        return int(value) if value and value.strip() else None
    except ValueError:
        return None
    
@app.get("/")
def read_root():
    with SessionLocal() as db:
        user_exists = db.query(Admin).first()
    if not user_exists:
        return RedirectResponse(url="/setup", status_code=303)
    return RedirectResponse(url="/login", status_code=303)

@app.get("/setup")
def show_setup(request: Request):
    with SessionLocal() as db:
        existing_admin = db.query(Admin).first()

    if existing_admin:
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse(request, "setup1.html", {})

def normalize_username(value: str) -> str:
    return clean_text(value, 50)


def find_account(db, username: str):
    """The account with this username, ignoring capital letters ("Ana.Cruz" finds "ana.cruz")."""
    if not username:
        return None
    return (db.query(Admin).filter(func.lower(Admin.username) == username.lower())
            .order_by(Admin.id).first())
 
@app.post("/setup")
def process_setup(request: Request, username: str = Form(...), password: str = Form(...), confirm_password: str = Form(...)):
    username = clean_text(username, 50)
    
    if not username: 
        return templates.TemplateResponse(request, "setup1.html",{"error": "Username is required."}, status_code=400)

    error = validate_new_password(password, confirm_password)

    if error:
        return templates.TemplateResponse(request, "setup1.html", {"error": error},status_code=400)

    recovery_key = generate_recovery_key()
    db = SessionLocal()
    try:
        # Acquire a write reservation before checking for an admin.
        db.execute(text("BEGIN IMMEDIATE"))

        if db.query(Admin).first() is not None:
            db.rollback()

            return RedirectResponse(url="/login", status_code=303)
            
        admin_role = db.query(Role).filter(Role.is_admin.is_(True)).first()
        new_admin = Admin(username=username, full_name=username, role_id=admin_role.id, active=True,
                          password_hash=hash_password(password), recovery_key_hash=hash_password(recovery_key))
        db.add(new_admin)
        db.commit()
        
    except IntegrityError:
        db.rollback()
        return templates.TemplateResponse(request,"setup1.html",{"error": "The account could not be created. Please try again."}, status_code=409)

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    log_action("Security", "Account Created", f'Created admin account "{username}" (first-time setup)', username=username)
    
    response = templates.TemplateResponse(request, "setup2.html", {"recovery_key": recovery_key})
    return response


@app.get("/login")
def show_login(request: Request):
    return templates.TemplateResponse(request, "login.html", {})


@app.post("/login")
def process_login(request: Request, username: str = Form(...), password: str = Form(...)):
    wait = throttle_wait("login")
    username = normalize_username(username)
    if wait:
        return templates.TemplateResponse(request, "login.html",
            {"error": f"Too many failed attempts. Try again in {wait} seconds."}, status_code=429)

    with SessionLocal() as db:
        admin = find_account(db, username)

    if not check_password(password, admin.password_hash if admin else None):
        record_failure("login")
        log_action("Security", "Login Failed",
                   f'Failed login attempt for username "{username[:50]}"', username=NOT_SIGNED_IN)
        return templates.TemplateResponse(request, "login.html", {"error": "Invalid username or password"})

    clear_failures("login")
    if not admin.active:
        log_action("Security", "Login Blocked", "Tried to sign in to a turned-off account", username=admin.username)
        return templates.TemplateResponse(request, "login.html",
            {"error": "This account is turned off. Ask the administrator to turn it back on."}, status_code=403)
    with SessionLocal() as db:
        db.query(Admin).filter(Admin.id == admin.id).update({"last_login_at": datetime.now()})
        db.commit()
        role = db.get(Role, admin.role_id) if admin.role_id else None
    global active_admin_id
    request.session.clear()
    request.session["admin_id"] = admin.id
    request.session["pwv"] = password_fingerprint(admin.password_hash)
    active_admin_id = admin.id
    log_action("Security", "Login", "Signed in", username=admin.username)
    if admin.must_change_password:
        return RedirectResponse(url="/first-login", status_code=303)
    keys = perms.parse(role.permissions) if role else frozenset()
    home = perms.home_page(keys, bool(role and role.is_admin))
    return RedirectResponse(url=home if branding.is_setup_complete() else "/wizard", status_code=303)


@app.get("/forgot-password")
def show_forgot_password(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html", {})

@app.post("/forgot-password")
def process_forgot_password(
    request: Request,
    username: str = Form(...),
    recovery_key: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    wait = throttle_wait("recovery")
    username = normalize_username(username)
    if wait:
        return templates.TemplateResponse(request, "forgot_password.html",
            {"error": f"Too many attempts. Try again in {wait} seconds."}, status_code=429)

    error, new_recovery_key = None, None
    with SessionLocal() as db:
        admin = find_account(db, username)
        stored = admin.recovery_key_hash if admin else None
        if not check_password(recovery_key.strip().upper(), stored):
            error = "Invalid username or recovery key"
        elif not admin.active:
            error = "This account is turned off. Ask the administrator to turn it back on."
        else:
            error = validate_new_password(new_password, confirm_password)

        if not error:
            new_recovery_key = generate_recovery_key()
            admin.password_hash = hash_password(new_password)
            admin.recovery_key_hash = hash_password(new_recovery_key)
            db.commit()          # `admin` is still attached to `db` here, so this really saves
            username = admin.username            # as saved, for the audit log

    if error:
        if error.startswith("Invalid"):
            record_failure("recovery")
            log_action("Security", "Recovery Failed",
                       f'Failed password reset attempt for username "{username[:50]}"', username=NOT_SIGNED_IN)
        return templates.TemplateResponse(request, "forgot_password.html", {"error": error})

    clear_failures("recovery")
    log_action("Security", "Password Reset",
               "Password reset using the recovery key; a new recovery key was issued", username=username)
    return templates.TemplateResponse(request, "recovery_key_display.html", {"recovery_key": new_recovery_key})

@app.post("/logout")
def logout(request: Request):
    global active_admin_id
    if request.session.get("admin_id"):
        log_action("Security", "Logout", "Signed out", request)   # before the session is cleared
    active_admin_id = None
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)

class NotAuthenticated(Exception):
    pass


@app.exception_handler(NotAuthenticated)
async def not_authenticated_handler(request: Request, exc: NotAuthenticated):
    return RedirectResponse(url="/login", status_code=303)

class SetupRequired(Exception):
    pass


@app.exception_handler(SetupRequired)
async def setup_required_handler(request: Request, exc: SetupRequired):
    return RedirectResponse(url="/wizard", status_code=303)


class StaffUser:
    """The person signed in for this request, with their role (read fresh on every request, so a
    change the administrator makes applies on that person's next click)."""
    __slots__ = ("id", "username", "full_name", "role_name", "is_admin", "perms", "must_change")

    def __init__(self, admin: Admin, role: Role):
        self.id, self.username = admin.id, admin.username
        self.full_name = (admin.full_name or admin.username or "").strip()
        self.role_name, self.is_admin = role.name, bool(role.is_admin)
        self.perms = frozenset(perms.KEYS) if self.is_admin else perms.parse(role.permissions)
        self.must_change = bool(admin.must_change_password)


def check_signed_in(request: Request):
    admin_id = request.session.get("admin_id")
    if not admin_id:
        raise NotAuthenticated()
    with SessionLocal() as db:
        admin = db.get(Admin, admin_id)
        role = db.get(Role, admin.role_id) if admin is not None and admin.role_id else None
        valid = (admin is not None and admin.active and role is not None
                 and request.session.get("pwv") == password_fingerprint(admin.password_hash))
        if valid:
            request.state.user = StaffUser(admin, role)
    if not valid:                         # account deleted or turned off, or password changed/reset elsewhere
        request.session.clear()
        raise NotAuthenticated()
    global active_admin_id
    active_admin_id = admin_id
    ensure_daily_rollover()
    
def login_required(request: Request):
    """Every normal page: signed in AND the first-run setup finished. Pages anyone signed in may
    open (About, Change password, pictures) use this alone; every other page uses require()."""
    check_signed_in(request)
    if request.state.user.must_change:
        raise FirstLoginRequired()
    if not branding.is_setup_complete():
        raise SetupRequired()


class NotAllowed(Exception):
    pass


class FirstLoginRequired(Exception):
    """Signed in with a temporary password: choosing their own comes before anything else."""


@app.exception_handler(FirstLoginRequired)
async def first_login_handler(request: Request, exc: FirstLoginRequired):
    return RedirectResponse(url="/first-login", status_code=303)


@app.exception_handler(NotAllowed)
async def not_allowed_handler(request: Request, exc: NotAllowed):
    user = getattr(request.state, "user", None)
    return templates.TemplateResponse(request, "no_access.html",
        {"role": user.role_name if user else "", "home": home_url(request)}, status_code=403)


def can(request: Request, key: str) -> bool:
    """True if the person signed in may do `key` (a key from permissions.py). Used by templates too."""
    user = getattr(request.state, "user", None)
    if not user:
        return False
    if key == perms.ADMIN_ONLY:
        return user.is_admin
    return user.is_admin or key in user.perms


def home_url(request: Request) -> str:
    """The first page this person's role allows (where they land after logging in)."""
    user = getattr(request.state, "user", None)
    return perms.home_page(user.perms, user.is_admin) if user else "/login"


def can_any(request: Request, keys) -> bool:
    """True if the role allows any of `keys`; an empty list means everyone signed in (the sidebar uses this)."""
    return not keys or any(can(request, k) for k in keys)


templates.env.globals.update(can=can, can_any=can_any, home_url=home_url)


def require(*keys: str):
    """A page's guard: signed in, setup finished, and the role allows at least one of `keys`.
    Usage: @app.get("/attendance", dependencies=[Depends(require("attendance"))])"""
    unknown = [k for k in keys if k not in perms.KEYS]
    if unknown:
        raise ValueError(f"Unknown permission {unknown}")      # a typo fails at start-up, not silently

    def check(request: Request):
        login_required(request)
        if not any(can(request, k) for k in keys):
            log_action_throttled(request, "Security", "Access Denied", f"Tried to open {request.url.path[:80]}",
                                 about=":" + "/".join(keys))
            raise NotAllowed()
    return check


def wizard_login(request: Request):
    """The wizard's own pages: signed in as an Administrator, without the setup gate (they ARE the setup)."""
    check_signed_in(request)
    if request.state.user.must_change:
        raise FirstLoginRequired()
    if not request.state.user.is_admin:
        raise NotAllowed()


def admin_required(request: Request):
    """Staff accounts and roles: the built-in Administrator role only (no tick box can give this)."""
    login_required(request)
    if not request.state.user.is_admin:
        raise NotAllowed()

@app.get("/change-password", dependencies=[Depends(login_required)])
def show_change_password(request: Request):
    return templates.TemplateResponse(request, "change_password.html", {})


@app.post("/change-password", dependencies=[Depends(login_required)])
def process_change_password(request: Request, current_password: str = Form(...), new_password: str = Form(...), confirm_password: str = Form(...)):
    wait = throttle_wait("change_password")
    if wait:
        return templates.TemplateResponse(request, "change_password.html",
            {"error": f"Too many failed attempts. Try again in {wait} seconds."}, status_code=429)
        
    error = None
    with SessionLocal() as db:
        admin = db.get(Admin, request.session["admin_id"])
        if not admin:
            request.session.clear()
            return RedirectResponse(url="/login", status_code=303)

        if not check_password(current_password, admin.password_hash):
            error = "Current password is incorrect"
            record_failure("change_password")
        else:
            error = validate_new_password(new_password, confirm_password)

        if not error:
            new_hash = hash_password(new_password)
            admin.password_hash = new_hash
            db.commit()
            request.session["pwv"] = password_fingerprint(admin.password_hash)   # keeps THIS window signed in

        if error:
            if error == "Current password is incorrect":
                log_action("Security", "Password Change Failed", "Wrong current password entered", request)
            return templates.TemplateResponse(request, "change_password.html", {"error": error})

        
    log_action("Security", "Password Changed", "Changed their own password", request)
    return templates.TemplateResponse(request, "change_password.html", {"success": "Password updated."})

def generate_recovery_key():
    """Generates a key like BQS-123A-456B-789C"""
    chars = string.ascii_uppercase + string.digits
    groups = ["".join(secrets.choice(chars) for _ in range(4)) for _ in range(3)]
    return "BQS-" + "-".join(groups)

MAX_PASSWORD_BYTES = 72                      # bcrypt's hard limit
_DUMMY_HASH = bcrypt.hashpw(b"timing-equaliser", bcrypt.gensalt()).decode()

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()

def check_password(plain: str, hashed: str | None) -> bool:
    raw = plain.encode()
    if len(raw) > MAX_PASSWORD_BYTES:
        return False                         # never reaches bcrypt, so no 500
    ok = bcrypt.checkpw(raw, (hashed or _DUMMY_HASH).encode())   # same cost for unknown users
    return ok and hashed is not None

def validate_new_password(new: str, confirm: str) -> str | None:
    if new != confirm:
        return "Passwords do not match"
    if len(new) < 8:
        return "Password must be at least 8 characters"
    if len(new.encode()) > MAX_PASSWORD_BYTES:
        return "Password is too long (72 bytes at most)"
    return None

def password_fingerprint(password_hash: str) -> str:
    return password_hash[-16:]               # changes whenever the password changes

# Throttle: 5 failures per 60 secs per action
THROTTLE_MAX_FAILURES, THROTTLE_WINDOW_SECONDS = 5, 60
_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()

def throttle_wait(key: str) -> int:
    now = monotonic()
    with _failures_lock:
        recent = [t for t in _failures.get(key, []) if now - t < THROTTLE_WINDOW_SECONDS]
        _failures[key] = recent
        if len(recent) >= THROTTLE_MAX_FAILURES:
            return int(THROTTLE_WINDOW_SECONDS - (now - recent[0])) + 1
    return 0

def record_failure(key: str):
    with _failures_lock:
        _failures.setdefault(key, []).append(monotonic())

def clear_failures(key: str):
    with _failures_lock:
        _failures.pop(key, None)
        
        
def get_current_admin(request: Request):
    admin_id = request.session.get("admin_id")
    if not admin_id:
        return None
    with SessionLocal() as db:
        return db.query(Admin).filter(Admin.id == admin_id).first()

def admin_initials(request: Request):
    """Two-letter avatar text from the signed-in username (e.g. admin.secretary -> AS)."""
    admin = get_current_admin(request)
    name = (admin.full_name or admin.username) if admin else ""
    if not name:
        return "AD"
    parts = [p for p in re.split(r"[._\s-]+", name) if p and p[0].isalnum()]
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[:2].upper()


templates.env.globals["admin_initials"] = admin_initials

@app.get("/dashboard", dependencies=[Depends(require("dashboard"))])
def show_dashboard(request: Request):
    with SessionLocal() as db:
        
        services = db.query(Service).all()
        
        today_start = datetime.combine(date.today(), datetime.min.time())
        
        active_tickets = db.query(Ticket).filter(Ticket.created_at >= today_start).all()
        visits_today = db.query(Visit).filter(Visit.created_at >= today_start).all()
        
        service_counter = Counter()
        for t in active_tickets:
            service_counter[t.service_id] += 1
        for v in visits_today:
            service_counter[v.service_id] += 1

        service_volume = [
            {"name": s.service_name, "count": service_counter.get(s.id, 0)}
            for s in services
        ]
        service_volume.sort(key=lambda row: row["count"], reverse=True)

        max_count = max((row["count"] for row in service_volume), default=0)
        for row in service_volume:
            row["percent"] = round(row["count"] / max_count * 100) if max_count else 0
            
        # Stat cards
        waiting_count = db.query(Ticket).filter(
            Ticket.status == "waiting", Ticket.created_at >= today_start
        ).count()

        completed_count = db.query(Visit).filter(
            Visit.final_status == "completed", Visit.created_at >= today_start
        ).count()

        no_show_count = db.query(Visit).filter(
            Visit.final_status == "no_show", Visit.created_at >= today_start
        ).count()

        # Issued today = still active + already archived
        total_count = len(active_tickets) + len(visits_today)
        
        overview = []
        for t in active_tickets:
            overview.append({
                "ticket_number": t.ticket_number,
                "citizen_name": t.citizen_name,
                "service_name": t.service_label,
                "status": t.status,
                "sort_time": t.called_at or t.created_at,
            })
        for v in visits_today:
            overview.append({
                "ticket_number": v.ticket_number,
                "citizen_name": v.citizen_name,
                "service_name": v.service_label,
                "status": v.final_status,
                "sort_time": v.completed_at or v.created_at,
            })
            
        
        overview.sort(key=lambda row: row["sort_time"], reverse=True)
        overview = overview[:10]

        response =  templates.TemplateResponse(request, "dashboard.html", {
            "overview": overview,
            "total_count": total_count,
            "waiting_count": waiting_count,
            "completed_count": completed_count,
            "no_show_count": no_show_count,
            "services": services,
            "service_volume": service_volume    
        })
        return response

@app.get("/client_registration", dependencies=[Depends(require("register"))])
def show_registration(request: Request):
    return render_registration(request)

def queue_snapshot() -> dict:
    """Numbers for the Live Queue panel on the registration page."""
    
    db = SessionLocal()
    
    try:
        today_start = datetime.combine(date.today(), datetime.min.time())
        
        current = db.query(Ticket).filter(Ticket.status == "called", Ticket.created_at >= today_start) \
            .order_by(Ticket.called_at.desc()).first()
            
        now_serving = current.ticket_number if current else None
        
        def waiting(lane):
            return db.query(Ticket).filter(
                Ticket.status == "waiting", Ticket.lane == lane, Ticket.created_at >= today_start).count()
            
        waiting_priority = waiting("P")
        waiting_regular = waiting("R")
    finally:
        db.close()
        
    upcoming = get_incoming_order(limit=1)
    next_up = upcoming[0].ticket_number if upcoming else None
    
    return {
        "now_serving": now_serving,
        "next_up": next_up,
        "waiting_priority": waiting_priority,
        "waiting_regular": waiting_regular
    }

@app.post("/register", dependencies=[Depends(require("register"))])
def register_ticket(request: Request, citizen_name: str = Form(""), lane: str = Form(""), service_id: str = Form(""), address: str = Form("")):
    form = {"citizen_name": citizen_name, "lane": lane, "service_id": service_id, "address": address}
    name, sid = clean_text(citizen_name, 100), parse_optional_int(service_id)
    addr = clean_text(address, 150) or None
    
    if not name:
        return render_registration(request, "Client name is required.", form, 400)
    if not addr:                                   # required since step 8e; still never printed on the queue slip
        return render_registration(request, "Please enter the client's address (Purok / Street).", form, 400)
    if lane not in ("P", "R"):
        return render_registration(request, "Please choose Priority or Regular.", form, 400)
    if sid is None:
        return render_registration(request, "Please choose a service.", form, 400)

    with SessionLocal() as db:
        service = db.query(Service).filter(Service.id == sid, Service.service_status == "Active").first()
        if service is None:
            return render_registration(request, "Selected service is unavailable.", form, 400)
        service_name = service.service_name
        ticket_number = issue_ticket_number(db, lane)          
        created_at = datetime.now()
        
        ticket = Ticket(citizen_name=name, lane=lane, service_id=sid, address=addr,
                        ticket_number=ticket_number, created_at=created_at)
        
        
        db.add(ticket)
        db.commit()
        ticket_id = ticket.id

    url = f"/client_registration/confirmation/{ticket_id}"
    notify_display_update()
    if AUTO_PRINT_ON_REGISTER:
        sent = print_ticket(ticket_number, name, service_name, lane, created_at)
        url += f"?printed={1 if sent else 0}"
    notify_display_update()
    return RedirectResponse(url=url, status_code=303)

@app.get("/client_registration/confirmation/{ticket_id}", dependencies=[Depends(require("register"))])
def show_ticket_confirmation(request: Request, ticket_id: int, printed: str = ""):
    """printed: "1" = sent to the printer, "0" = the printer failed, "" = not printed yet."""
    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
        if ticket is None:
            # Already served or deleted, so there is nothing left to confirm
            return RedirectResponse(url="/client_registration", status_code=303)
        return templates.TemplateResponse(request, "ticket_confirmation.html", {
            "ticket": ticket,
            "printed": printed,
        })
    finally:
        db.close()


@app.post("/client_registration/confirmation/{ticket_id}/print", dependencies=[Depends(require("register"))])
def print_ticket_again(request: Request, ticket_id: int):
    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
        if ticket is None:
            return RedirectResponse(url="/client_registration", status_code=303)
        number = ticket.ticket_number
        sent = print_ticket(ticket.ticket_number, ticket.citizen_name,
                            ticket.service_label, ticket.lane, ticket.created_at)
    finally:
        db.close()
    log_action("Queue", "Slip Reprinted", f"Reprinted slip for {number}" + ("" if sent else " (printer error)"), request)
    return RedirectResponse(
        url=f"/client_registration/confirmation/{ticket_id}?printed={1 if sent else 0}",
        status_code=303,
    )

def issue_ticket_number(db, lane: str) -> str:
    """Next number for today's lane. The write takes SQLite's lock, which is held until commit,
    so two registrations can never receive the same number and numbers are never reused."""
    n = db.execute(text("""
        INSERT INTO daily_counters (day, lane, last_number) VALUES (:day, :lane, 1)
        ON CONFLICT (day, lane) DO UPDATE SET last_number = last_number + 1
        RETURNING last_number"""),
        {"day": date.today().isoformat(), "lane": lane}).scalar_one()
    return f"{lane}-{n:03d}"

@app.post("/tickets/delete/{ticket_id}", dependencies=[Depends(require("register"))])
def delete_ticket(request: Request, ticket_id: int):
    """Remove a ticket from the live queue. Nothing is written to attendance history."""
    with SessionLocal() as db:
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.status == "waiting").first()
        number = ticket.ticket_number if ticket else None
        if ticket:
            db.delete(ticket)
            db.commit()
    if number:
        log_action("Queue", "Ticket Deleted", f"Removed waiting ticket {number}; not saved to attendance", request)
        notify_display_update()
    return RedirectResponse(url="/client_registration", status_code=303)

# QUEUE MANAGEMENT
@app.get("/queue_management", dependencies=[Depends(require("queue"))])
def show_queue(request: Request):
    if "announce" in request.query_params:        # the announcement now comes from the session, never the URL
        return RedirectResponse(url="/queue_management", status_code=303)
    
    with SessionLocal() as db:
        tickets = db.query(Ticket).all()
        services = db.query(Service).all()
        upcomming = get_incoming_order(limit=3)
        active_statuses = ["waiting", "called", "skipped"]
        
        today_start = datetime.combine(date.today(), datetime.min.time())
        
        current_ticket = db.query(Ticket).filter(Ticket.status == "called", Ticket.created_at >= today_start) \
            .order_by(Ticket.called_at.desc()).first()

        all_active = db.query(Ticket).filter(Ticket.status.in_(active_statuses), Ticket.created_at >= today_start).count()
        waiting_count = db.query(Ticket).filter(Ticket.status == "waiting", Ticket.created_at >= today_start).count()
        called_count = db.query(Ticket).filter(Ticket.status == "called", Ticket.created_at >= today_start).count()
        skipped_count = db.query(Ticket).filter(Ticket.status == "skipped", Ticket.created_at >= today_start).count()
                
        total_today = db.query(Ticket).filter(Ticket.created_at >= today_start).count()
        
                # "Prepare document": only when the visitor's service is linked to a document that is turned on
        prepare = None
        linked_id = current_ticket.service.document_template_id if current_ticket and current_ticket.service else None
        linked = db.get(DocumentTemplate, linked_id) if linked_id else None
        if linked and linked.is_active and can(request, "issue"):    # only for people who may issue
            prepare = {"url": f"/documents/issue/{linked.id}?ticket={current_ticket.id}", "name": linked.name}
            
        announce = request.session.pop("announce", None)        # one-shot: gone after this page load
        announce_clips = build_announcement_clips(announce) if announce else []      
              
        message = request.session.pop("flash", None)
        response =  templates.TemplateResponse(request, "queue_management.html", {
            
            "total_today": total_today,
            "tickets": tickets,
            "services": services,
            "all_active": all_active,
            "waiting_count": waiting_count,
            "called_count": called_count,
            "skipped_count": skipped_count,
            "current_ticket": current_ticket,
            "upcomming": upcomming,
            "announcement_clips": announce_clips,
            "message": message,
            "prepare": prepare
        })
        
        return response


# LIVE PANEL
@app.post("/call-next/{ticket_id}", dependencies=[Depends(require("queue"))])
def call_next(request: Request, ticket_id: int):
    upcoming = get_incoming_order(limit=1)
    if not upcoming:
        flash(request, False, "Nobody is waiting.")
        return RedirectResponse(url="/queue_management", status_code=303)
    
    target = upcoming[0]
    
    if target.id != ticket_id:       # the queue changed since the page was drawn
        flash(request, False, f"The queue changed. The next visitor is now {target.ticket_number}. "
                              "Please check and press Call next again.")
        return RedirectResponse(url="/queue_management", status_code=303)
    
    with SessionLocal() as db:
        claimed = db.execute(_CLAIM, {"id": target.id, "from_status": "waiting", "now": _stamp(), "today_start": _today_start_str()}).rowcount == 1
        db.commit()
    if not claimed:
        flash(request, False, "Someone is already at the counter. Finish or skip that visitor first.")# someone else got there first
        return RedirectResponse(url="/queue_management", status_code=303)
    
    request.session["announce"] = target.ticket_number      # played once (see #7)
    log_action("Queue", "Ticket Called", f"Called {target.ticket_number}", request)
    notify_display_update()
    return RedirectResponse(url="/queue_management", status_code=303)


def finish_ticket(ticket_id: int, final_status: str, allowed_statuses: tuple):
    """Atomically archive an eligible ticket exactly once."""
    
    db = SessionLocal()

    try:
        # Serialize this database write sequence.
        db.execute(text("BEGIN IMMEDIATE"))
        
        ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
        if ticket is None or ticket.status not in allowed_statuses:
            db.rollback()
            return None

        # Copy everything needed BEFORE deleting: the object is unusable once its row is gone
        number = ticket.ticket_number
        visit = Visit(
            citizen_name=ticket.citizen_name,
            address=ticket.address,
            lane=ticket.lane,
            ticket_number=number,
            final_status=final_status,
            service_id=ticket.service_id,
            created_at=ticket.created_at,
            called_at=ticket.called_at,
            completed_at=datetime.now(),
        )

        # Delete FIRST and check how many rows it removed. If two requests arrive together, only
        # one of them really removes the row, and only that one saves a Visit.
        removed = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.status.in_(allowed_statuses)).delete(synchronize_session=False)
        if removed != 1:
            db.rollback()
            return None

        db.add(visit)
        db.commit()   # the delete and the new Visit are saved together, or not at all
        return number
    
    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
    
def get_incoming_order(limit: int = 3):
    """Simulate the 2:1 priority:regular call order without actually calling anyone."""
    
    start = datetime.combine(date.today(), time.min)
    with SessionLocal() as db:
        streak = current_priority_streak(db)
        waiting = db.query(Ticket).filter(Ticket.status == "waiting", Ticket.created_at >= start) \
                    .order_by(Ticket.created_at)
        waiting_priority = waiting.filter(Ticket.lane == "P").all()
        waiting_regular = waiting.filter(Ticket.lane == "R").all()

    upcoming, p_index, r_index = [], 0, 0
    while len(upcoming) < limit and (p_index < len(waiting_priority) or r_index < len(waiting_regular)):
        if streak < 2 and p_index < len(waiting_priority):
            upcoming.append(waiting_priority[p_index]); p_index += 1; streak += 1
        elif r_index < len(waiting_regular):
            upcoming.append(waiting_regular[r_index]); r_index += 1; streak = 0
        else:
            upcoming.append(waiting_priority[p_index]); p_index += 1; streak += 1
    return upcoming

def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")   # same format SQLAlchemy stores


@app.post("/skip/{ticket_id}", dependencies=[Depends(require("queue"))])
def skip_ticket(request: Request, ticket_id: int):
    
    with SessionLocal() as db:
        changed = db.query(Ticket).filter(Ticket.id == ticket_id, Ticket.status == "called") \
                    .update({"status": "skipped"}, synchronize_session=False)
        number = db.query(Ticket.ticket_number).filter(Ticket.id == ticket_id).scalar() if changed == 1 else None
        db.commit()
    if number:
        log_action("Queue", "Ticket Skipped", f"Skipped {number}", request)
        notify_display_update()                      # from #6
    return RedirectResponse(url="/queue_management", status_code=303)


@app.post("/recall/{ticket_id}", dependencies=[Depends(require("queue"))])
def recall_ticket(request: Request, ticket_id: int):
    with SessionLocal() as db:
        claimed = db.execute(_CLAIM, {"id": ticket_id, "from_status": "skipped", "now": _stamp(), "today_start": _today_start_str()}).rowcount == 1
        db.commit()
        number = db.query(Ticket.ticket_number).filter(Ticket.id == ticket_id).scalar() if claimed else None
    if number is None:
        return RedirectResponse(url="/queue_management", status_code=303)
    request.session["announce"] = number
    log_action("Queue", "Ticket Recalled", f"Recalled skipped ticket {number}", request)
    notify_display_update()
    return RedirectResponse(url="/queue_management", status_code=303)

@app.post("/complete/{ticket_id}", dependencies=[Depends(require("queue"))])
def complete_ticket(request: Request, ticket_id: int):
    # Only the visitor at the counter can be completed
    number = finish_ticket(ticket_id, "completed", ("called",))
    if number:
        log_action("Queue", "Ticket Completed", f"Marked {number} as completed", request)
        notify_display_update()
    return RedirectResponse(url="/queue_management", status_code=303)


@app.post("/no-show/{ticket_id}", dependencies=[Depends(require("queue"))])
def no_show_ticket(request: Request, ticket_id: int):
    # The visitor at the counter, or one in the skipped list
    number = finish_ticket(ticket_id, "no_show", ("called", "skipped"))
    if number:
        log_action("Queue", "Ticket No-Show", f"Marked {number} as no-show", request)
        notify_display_update()
    return RedirectResponse(url="/queue_management", status_code=303)

@app.post("/queue/clear", dependencies=[Depends(require("queue"))])
def clear_queue(request: Request):
    """End-of-day cleanup: every unserved ticket becomes a no-show in attendance history.

    Ticket numbers do NOT restart: they are counted from today's tickets AND visits.
    """
    db = SessionLocal()
    counts = Counter()
    try:
        db.execute(text("BEGIN IMMEDIATE"))
        
        tickets = db.query(Ticket).filter(Ticket.status.in_(["waiting", "called", "skipped"])).all()
        now = datetime.now()
        for t in tickets:
            counts[t.status] += 1
            db.add(Visit(
                citizen_name=t.citizen_name, lane=t.lane, ticket_number=t.ticket_number,
                final_status="no_show", service_id=t.service_id, address=t.address,
                created_at=t.created_at, called_at=t.called_at, completed_at=now,
            ))
            db.delete(t)
        db.commit()
    finally:
        db.close()

    total = sum(counts.values())
    if total:
        log_action("Queue", "Queue Cleared",
                   f"Cleared {total} unserved ticket(s) ({counts['waiting']} waiting, {counts['called']} called, "
                   f"{counts['skipped']} skipped); all saved as no-show", request)
        notify_display_update()
    return RedirectResponse(url="/queue_management", status_code=303)

@app.get("/attendance", dependencies=[Depends(require("attendance"))])
def show_attendance(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    lane: str = "",
    service_id: str = "",
    q: str = "",
    page: str = "1",
):  
    service_id_int = parse_optional_int(service_id)
    
    if service_id and parse_optional_int(service_id) is None:
        return RedirectResponse(str(request.url.remove_query_params("service_id")), status_code=303)
    
    db = SessionLocal()
    try:
        # Single day filter - defaults to today when `date` is empty.
        # (resolve_date_range takes a from/to pair; passing `date` for both
        # gives one day, exactly like the print route's single-day case.)
        d_from, d_to = resolve_date_range(date_from, date_to)

        base = apply_visit_filters(db.query(Visit), d_from, d_to, lane, service_id_int, q)

        total_records = base.count()                                   # count BEFORE offset/limit
        page = parse_optional_int(page) or 1
        total_pages = max(1, math.ceil(total_records / PAGE_SIZE))
        page = max(1, min(page, total_pages))                           # clamp a stale ?page= from the URL

        visits = (
            base.order_by(Visit.created_at.desc())
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
            .all()
        )

        start_record = (page - 1) * PAGE_SIZE + 1 if total_records else 0
        end_record = min(page * PAGE_SIZE, total_records)

        services = db.query(Service).all()

        response = templates.TemplateResponse(request, "attendance.html", {
            "visits": visits,
            "services": services,
            
            "date_from": d_from.isoformat(),   # feeds the "From Date" input
            "date_to": d_to.isoformat(),
            
            "lane": lane,
            "service_id": service_id_int,
            "search": q,
            "page": page,
            "total_pages": total_pages,
            "total_records": total_records,
            "start_record": start_record,
            "end_record": end_record,
        })
    finally:
        db.close()
    return response

def resolve_date_range(date_from: str, date_to: str):
    """
    The date filter always has a value - there is no "all dates".
    - nothing given         -> today
    - only date_from given  -> that single day
    - both given            -> that range (swapped if entered backwards)
    """
    d_from = parse_date(date_from) or date.today()
    d_to = parse_date(date_to) or d_from
    if d_to < d_from:
        d_from, d_to = d_to, d_from
    return d_from, d_to

def range_label(d_from, d_to):
    fmt = lambda d: d.strftime("%b %d, %Y")
    if d_from == d_to:
        return fmt(d_from)                     # e.g. "Oct 24, 2026"
    return f"{fmt(d_from)} to {fmt(d_to)}"     # e.g. "Oct 24, 2026 to Oct 30, 2026"

def apply_visit_filters(query, d_from, d_to, lane, service_id, q):
    """Use this in BOTH /attendance and /attendance/print so they never disagree."""
    start_dt = datetime.combine(d_from, time.min)   # d_from 00:00:00.000000
    end_dt = datetime.combine(d_to, time.max)        # d_to   23:59:59.999999
    query = query.filter(Visit.created_at >= start_dt, Visit.created_at <= end_dt)

    if lane:
        query = query.filter(Visit.lane == lane)
    if service_id:
        query = query.filter(Visit.service_id == service_id)
    if q:
        like = like_pattern(q)
        query = query.filter(Visit.ticket_number.ilike(like, escape="\\")
                             | Visit.citizen_name.ilike(like, escape="\\")
                             | Visit.address.ilike(like, escape="\\"))
    return query

def like_pattern(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"

@app.get("/attendance/print", dependencies=[Depends(require("attendance"))])
def attendance_print(request: Request, date_from: str = "", date_to: str = "", lane: str = "", service_id: str = "", q: str = ""):
    
    service_id_int = parse_optional_int(service_id)
    if service_id and parse_optional_int(service_id) is None:
        return RedirectResponse(str(request.url.remove_query_params("service_id")), status_code=303)
    
    db = SessionLocal()
    
    try:
        d_from, d_to = resolve_date_range(date_from, date_to)
        
        base = apply_visit_filters(db.query(Visit), d_from, d_to, lane, service_id_int, q)
        
        completed = base.filter(Visit.final_status == "completed").count()
        no_show = base.filter(Visit.final_status == "no_show").count()
        stats = {
            "issued": completed + no_show,
            "completed": completed,
            "no_show": no_show
        }
        
        rows = base.order_by(Visit.created_at).all()
        

        # Human-readable filter summary for the toolbar
        service = db.get(Service, service_id_int) if service_id_int else None
        
        parts = [
            range_label(d_from, d_to),
            lane.title() if lane else "All Types",
            service.service_name if service else "All Services",
        ]
        if q:
            parts.append(f'Search: "{q}"')
            
        log_action_throttled(request, "Database", "Attendance Printed", f"Opened the attendance print view ({', '.join(parts)})")
        return templates.TemplateResponse(request,"print_attendance.html", {
            
            "stats": stats,
            "rows": rows,
            "total_rows": len(rows),
            "filters_text": ", ".join(parts),
            "range_text": range_label(d_from, d_to),
            "generated_at": datetime.now().strftime("%m/%d/%Y %I:%M %p"),
        },
    )
    finally:
        db.close()
        
_CSV_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")
def csv_safe(value) -> str:
    """Stop Excel from running a cell that starts with = + - @ as a formula."""
    s = "" if value is None else str(value)
    return "'" + s if s.startswith(_CSV_DANGEROUS) else s


@app.get("/attendance/export", dependencies=[Depends(require("attendance"))])
def attendance_export(request: Request, date_from: str = "", date_to: str = "", lane: str = "", service_id: str = "", q: str = ""):
    """Download the attendance table (same filters as the page) as a CSV file."""
    service_id_int = parse_optional_int(service_id)
    d_from, d_to = resolve_date_range(date_from, date_to)

    if service_id and parse_optional_int(service_id) is None:
        return RedirectResponse(str(request.url.remove_query_params("service_id")), status_code=303)
    db = SessionLocal()
    try:
        rows = apply_visit_filters(db.query(Visit), d_from, d_to, lane, service_id_int, q) \
            .order_by(Visit.created_at).all()

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Date", "Ticket", "Client name", "Address", "Type", "Service", "Time in", "Time out", "Status"])
        for v in rows:
            writer.writerow([
                v.created_at.strftime("%m/%d/%Y"),
                csv_safe(v.ticket_number),
                csv_safe(v.citizen_name),
                csv_safe(v.address),
                "Priority" if v.lane == "P" else "Regular",
                csv_safe(v.service.service_name if v.service else "Removed service"),
                v.called_at.strftime("%I:%M %p") if v.called_at else "",
                v.completed_at.strftime("%I:%M %p") if v.completed_at else "",
                (v.final_status or "").replace("_", " ").title(),
            ])
    finally:
        db.close()

    log_action("Database", "CSV Export", f"Exported {len(rows)} attendance record(s) for {range_label(d_from, d_to)}", request)
    return Response(
        content="\ufeff" + buffer.getvalue(),   # BOM so Excel reads UTF-8 names correctly
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="attendance_{d_from}_to_{d_to}.csv"'},
    )
    
    
# SERVICE
def service_name_error(db, name: str, exclude_id: int | None = None) -> str | None:
    if not name:
        return "Service name is required."
    q = db.query(Service).filter(func.lower(Service.service_name) == name.lower())
    if exclude_id:
        q = q.filter(Service.id != exclude_id)
    return f'A service named "{name}" already exists.' if q.first() else None

def active_template_choices(db) -> list:
    """The documents a service can be linked to: only ones that are turned on."""
    return [{"id": t.id, "name": t.name} for t in
            db.query(DocumentTemplate).filter(DocumentTemplate.is_active.is_(True)).order_by(DocumentTemplate.id).all()]

def read_document_choice(db, value: str):
    """The 'Document to issue' dropdown. Returns (template id or None, error message or None)."""
    if not (value or "").strip():
        return None, None                                   # "None": no Prepare document button
    template_id = parse_optional_int(value)
    row = db.get(DocumentTemplate, template_id) if template_id else None
    if row is None or not row.is_active:
        return None, "That document is turned off or was removed. Choose another one, or None. Nothing was saved."
    return row.id, None

def template_name_of(db, template_id) -> str:
    row = db.get(DocumentTemplate, template_id) if template_id else None
    return row.name if row else "None"

@app.get("/services", dependencies=[Depends(require("services"))])
def list_services(request: Request):
    message = request.session.pop("flash", None)

    with SessionLocal() as db:
        services = db.query(Service).all()
        today_start = datetime.combine(date.today(), datetime.min.time())

        total_today = db.query(Ticket).filter(Ticket.created_at >= today_start).count()
        doc_names = {t.id: t.name for t in db.query(DocumentTemplate).all()}
        return templates.TemplateResponse(request, "services.html", {

            "services": services,
            "total_today": total_today,
            "message": message,
            "template_choices": active_template_choices(db),
            "doc_names": doc_names,

        })

@app.post("/services/add", dependencies=[Depends(require("services"))])
def add_services(request: Request, service_name: str = Form(...), service_status: StatusEnum = Form(alias="service_status"),
                 document_template_id: str = Form("")):
    name = clean_text(service_name, 80)
    with SessionLocal() as db:
        error = service_name_error(db, name)
        template_id, doc_error = read_document_choice(db, document_template_id)
        if error or doc_error:
            flash(request, False, error or doc_error)
            return RedirectResponse(url="/services", status_code=303)
        db.add(Service(service_name=name, service_status=service_status.value, document_template_id=template_id))
        db.commit()
        doc_note = f"; document: {template_name_of(db, template_id)}" if template_id else ""
    log_action("Service", "Service Added", f"Added service: {name} ({service_status.value}){doc_note}", request)
    return RedirectResponse(url="/services", status_code=303)

@app.post("/services/edit/{service_id}", dependencies=[Depends(require("services"))])
def edit_service(request: Request, service_id: int, service_name: str = Form(...), service_status: StatusEnum = Form(alias="service_status"),
                 document_template_id: str = Form("")):
    changes = []
    name = clean_text(service_name, 80)
    with SessionLocal() as db:
        service = db.get(Service, service_id)
        error = service_name_error(db, name, exclude_id=service_id)
        template_id, doc_error = read_document_choice(db, document_template_id)

        if error or doc_error:
            flash(request, False, error or doc_error)
            return RedirectResponse(url="/services", status_code=303)

        if service:
            if service.service_name != name:
                changes.append(f'renamed from "{service.service_name}"')
            if service.service_status != service_status.value:
                changes.append(f"status {service.service_status} -> {service_status.value}")
            if service.document_template_id != template_id:
                changes.append(f"document {template_name_of(db, service.document_template_id)} -> {template_name_of(db, template_id)}")
            service.service_name = name
            service.service_status = service_status.value
            service.document_template_id = template_id
            db.commit()
    if changes:   # saving the form without changing anything is not worth a log row
        log_action("Service", "Service Modified", f'Modified service "{name}": ' + "; ".join(changes), request)

    return RedirectResponse(url="/services", status_code=303)

@app.post("/services/delete/{service_id}", dependencies=[Depends(require("services"))])
def delete_service(request: Request, service_id: int):
    db = SessionLocal()
    log_entry = None
    try:
        service = db.query(Service).filter(Service.id == service_id).first()
        if service:
            name = service.service_name
            has_history = (
                db.query(Ticket).filter(Ticket.service_id == service_id).first() is not None
                or db.query(Visit).filter(Visit.service_id == service_id).first() is not None
            )
            if has_history:
                # Old tickets and visits still point at this service, so keep the row
                service.service_status = "Inactive"
                log_entry = ("Service Deactivated", f'Deleted service "{name}", but it has ticket history, so it was set to Inactive instead')
            else:
                try:
                    db.delete(service)
                    db.commit()
                    log_entry = ("Service Deleted", f"Deleted service: {name}")
                except IntegrityError:         # a ticket appeared a moment ago: keep the row
                    db.rollback()
                    db.get(Service, service_id).service_status = "Inactive"
                    db.commit()
                    log_entry = ("Service Deactivated", f'"{name}" gained ticket history, so it was set to Inactive')
            db.commit()
    finally:
        db.close()
    if log_entry:
        log_action("Service", log_entry[0], log_entry[1], request)
    return RedirectResponse(url="/services", status_code=303)

# STAFF ACCOUNTS AND ROLES (step 9b). The built-in Administrator role only.
# People are never deleted, only turned off: their name stays on issued documents and in the audit log.

# Easy to read aloud and copy from paper. Used once: the person must replace it at first login.
TEMP_WORDS = ("Tala", "Bituin", "Ulap", "Dagat", "Bundok", "Ilog", "Araw", "Buwan", "Hangin", "Puno",
              "Bulaklak", "Palay", "Niyog", "Mangga", "Sampaguita", "Narra", "Agila", "Maya", "Perlas", "Bahaghari")
USERNAME_RULE = re.compile(r"[a-z0-9][a-z0-9._-]{2,29}")


def make_temp_password() -> str:
    """Like "Tala-4827": a word, a dash and four digits."""
    return f"{secrets.choice(TEMP_WORDS)}-{secrets.randbelow(10000):04d}"


def active_admin_count(db, excluding_id: int | None = None) -> int:
    """How many active accounts have the Administrator role (optionally not counting one person)."""
    q = (db.query(Admin).join(Role, Admin.role_id == Role.id)
         .filter(Role.is_admin.is_(True), Admin.active.is_(True)))
    if excluding_id is not None:
        q = q.filter(Admin.id != excluding_id)
    return q.count()


def staff_label(person: Admin) -> str:
    """For messages and the audit log: "Ana R. Cruz (ana.cruz)"."""
    return f"{person.full_name or person.username} ({person.username})"


def staff_back(request: Request, ok: bool, text_: str, url: str = "/staff"):
    flash(request, ok, text_)
    return RedirectResponse(url=url, status_code=303)


@app.get("/staff", dependencies=[Depends(admin_required)])
def show_staff(request: Request):
    message = request.session.pop("flash", None)
    with SessionLocal() as db:
        people = db.query(Admin).options(joinedload(Admin.role)).all()
        roles = db.query(Role).order_by(Role.is_admin.desc(), Role.name).all()
    people.sort(key=lambda p: (not p.active, not (p.role and p.role.is_admin), (p.full_name or p.username).lower()))
    per_role = {r.id: sum(1 for p in people if p.role_id == r.id and p.active) for r in roles}
    new_role = next((r for r in roles if r.name == perms.SECRETARY_ROLE), None) or next((r for r in roles if not r.is_admin), None)
    return templates.TemplateResponse(request, "staff.html", {
        "people": people, "roles": roles, "per_role": per_role, "message": message,
        "turned_off": sum(1 for p in people if not p.active),
        "default_role_id": new_role.id if new_role else roles[0].id,
        "my_id": request.state.user.id,
    })


@app.post("/staff/add", dependencies=[Depends(admin_required)])
def add_staff(request: Request, full_name: str = Form(""), username: str = Form(""), role_id: str = Form("")):
    name = clean_text(full_name, 60)
    user = clean_text(username, 30).lower()
    with SessionLocal() as db:
        role = db.get(Role, parse_optional_int(role_id) or 0)
        if not name:
            return staff_back(request, False, "Type the person's full name.")
        if not USERNAME_RULE.fullmatch(user):
            return staff_back(request, False, "The username needs 3 to 30 small letters or numbers, "
                                              "and may use a dot, dash or underscore (for example ana.cruz).")
        if find_account(db, user):
            return staff_back(request, False, f'The username "{user}" is already taken. Choose another one.')
        if role is None:
            return staff_back(request, False, "Choose a role.")
        temp = make_temp_password()
        person = Admin(username=user, full_name=name, role_id=role.id, active=True, must_change_password=True,
                       password_hash=hash_password(temp), recovery_key_hash=None)
        db.add(person)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return staff_back(request, False, f'The username "{user}" is already taken. Choose another one.')
        role_name = role.name
    log_action("Staff", "Staff Account Added", f"Added {name} ({user}) as {role_name}", request)
    # Shown once, straight from this response; the password is never stored anywhere readable.
    return templates.TemplateResponse(request, "staff_password.html", {
        "full_name": name, "username": user, "password": temp, "is_new": True})


@app.post("/staff/{person_id}/edit", dependencies=[Depends(admin_required)])
def edit_staff(request: Request, person_id: int, full_name: str = Form(""), role_id: str = Form("")):
    name = clean_text(full_name, 60)
    me = request.state.user.id
    with SessionLocal() as db:
        person = db.get(Admin, person_id)
        role = db.get(Role, parse_optional_int(role_id) or 0)
        if person is None:
            return staff_back(request, False, "That account no longer exists.")
        if not name:
            return staff_back(request, False, "Type the person's full name.")
        if role is None:
            return staff_back(request, False, "Choose a role.")
        old_role = db.get(Role, person.role_id) if person.role_id else None
        losing_admin = bool(old_role and old_role.is_admin and not role.is_admin)
        if losing_admin and person.id == me:
            return staff_back(request, False, "You can't take the Administrator role away from yourself. "
                                              "Another administrator can do it.")
        if losing_admin and person.active and active_admin_count(db, excluding_id=person.id) == 0:
            return staff_back(request, False, "At least one active account must keep the Administrator role.")
        changes = []
        if name != (person.full_name or ""):
            changes.append(f'name "{person.full_name}" to "{name}"')
        if role.id != person.role_id:
            changes.append(f"role {old_role.name if old_role else 'none'} to {role.name}")
        if not changes:
            return staff_back(request, True, "Nothing changed.")
        person.full_name, person.role_id = name, role.id
        db.commit()
        label = staff_label(person)
    log_action("Staff", "Staff Account Changed", f"{label}: changed " + "; ".join(changes), request)
    return staff_back(request, True, f"Saved the changes to {name}.")


@app.post("/staff/{person_id}/reset-password", dependencies=[Depends(admin_required)])
def reset_staff_password(request: Request, person_id: int):
    if person_id == request.state.user.id:
        return staff_back(request, False, "To change your own password, use Change password in your account menu.")
    with SessionLocal() as db:
        person = db.get(Admin, person_id)
        if person is None:
            return staff_back(request, False, "That account no longer exists.")
        temp = make_temp_password()
        person.password_hash = hash_password(temp)      # also signs them out wherever they are signed in
        person.must_change_password = True
        db.commit()
        name, user, label, active = person.full_name or person.username, person.username, staff_label(person), person.active
    log_action("Staff", "Password Reset by Administrator", f"Gave {label} a new temporary password", request)
    return templates.TemplateResponse(request, "staff_password.html", {
        "full_name": name, "username": user, "password": temp, "is_new": False, "turned_off": not active})


@app.post("/staff/{person_id}/turn-off", dependencies=[Depends(admin_required)])
def turn_off_staff(request: Request, person_id: int):
    if person_id == request.state.user.id:
        return staff_back(request, False, "You can't turn off your own account.")
    with SessionLocal() as db:
        person = db.get(Admin, person_id)
        if person is None or not person.active:
            return staff_back(request, True, "That account is already turned off.")
        role = db.get(Role, person.role_id) if person.role_id else None
        if role and role.is_admin and active_admin_count(db, excluding_id=person.id) == 0:
            return staff_back(request, False, "At least one active account must keep the Administrator role.")
        person.active = False
        db.commit()
        label, name = staff_label(person), person.full_name or person.username
    log_action("Staff", "Staff Account Turned Off", f"Turned off {label}", request)
    return staff_back(request, True, f"{name} can no longer log in. Their records stay as they are.")


@app.post("/staff/{person_id}/turn-on", dependencies=[Depends(admin_required)])
def turn_on_staff(request: Request, person_id: int):
    with SessionLocal() as db:
        person = db.get(Admin, person_id)
        if person is None or person.active:
            return staff_back(request, True, "That account is already turned on.")
        person.active = True
        db.commit()
        label, name = staff_label(person), person.full_name or person.username
    log_action("Staff", "Staff Account Turned On", f"Turned on {label}", request)
    return staff_back(request, True, f"{name} can log in again with their current password.")


# ---- Roles
def role_form_error(db, name: str, keys: frozenset, role_id: int | None = None) -> str | None:
    if not name:
        return "Give the role a name, for example Front desk or Treasurer."
    clash = db.query(Role).filter(func.lower(Role.name) == name.lower())
    if role_id is not None:
        clash = clash.filter(Role.id != role_id)
    if clash.first() is not None:
        return f'There is already a role called "{name}". Choose another name.'
    if not keys:
        return "Tick at least one thing this role may do."
    return None


def roles_page(request: Request, message=None, reopen: dict | None = None):
    """The Roles tab. Add role and Edit role are pop-up forms on it. `reopen` opens one of them on
    arrival: {"id": "new" or a role id}, plus "name", "keys" and "error" when a save was refused,
    so the person sees what went wrong with their name and ticks still filled in."""
    groups = {}
    for group, key, label, help_ in perms.PERMISSIONS:
        needs = perms.NEEDS.get(key)
        groups.setdefault(group, []).append((key, label, help_, needs, perms.LABELS[needs] if needs else ""))
    with SessionLocal() as db:
        roles = db.query(Role).order_by(Role.is_admin.desc(), Role.name).all()
        accounts = db.query(Admin).all()
    holders = {}                     # who has each role, for "can't be deleted yet" (turned-off accounts too)
    for a in sorted(accounts, key=lambda a: (not a.active, (a.full_name or a.username).lower())):
        holders.setdefault(a.role_id, []).append(a)
    cards = [{"role": r, "people": len(holders.get(r.id, [])), "holders": holders.get(r.id, []),
              "keys": perms.parse(r.permissions),
              "labels": [perms.LABELS[k] for k in perms.KEYS if k in perms.parse(r.permissions)]} for r in roles]
    return templates.TemplateResponse(request, "roles.html", {
        "cards": cards, "message": message, "groups": groups, "reopen": reopen or {},
    }, status_code=400 if reopen and reopen.get("error") else 200)


@app.get("/staff/roles", dependencies=[Depends(admin_required)])
def show_roles(request: Request):
    message = request.session.pop("flash", None)
    which = request.query_params.get("open", "")          # /staff/roles?open=new or ?open=<role id>
    reopen = {"id": "new"} if which == "new" else ({"id": int(which)} if which.isdigit() else None)
    return roles_page(request, message, reopen)


def refused(request: Request, role_id, name: str, keys, error: str):
    """A save that can't go through: back to the Roles tab with that pop-up open again."""
    return roles_page(request, reopen={"id": role_id, "name": name, "keys": set(keys), "error": error})


@app.get("/staff/roles/new", dependencies=[Depends(admin_required)])
def new_role_form(request: Request):
    return RedirectResponse(url="/staff/roles?open=new", status_code=303)


@app.post("/staff/roles/new", dependencies=[Depends(admin_required)])
def add_role(request: Request, name: str = Form(""), perm: list[str] = Form([])):
    name, keys = clean_text(name, 40), perms.with_needs(perms.parse(",".join(perm)))
    with SessionLocal() as db:
        error = role_form_error(db, name, keys)
        if error:
            return refused(request, "new", name, keys, error)
        db.add(Role(name=name, permissions=perms.dump(keys), is_admin=False))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return refused(request, "new", name, keys, f'There is already a role called "{name}".')
    log_action("Staff", "Role Added", f"Added the role {name}: " + ", ".join(perms.LABELS[k] for k in perms.KEYS if k in keys), request)
    return staff_back(request, True, f"Added the role {name}. You can now give it to staff accounts.", "/staff/roles")


@app.get("/staff/roles/{role_id}/edit", dependencies=[Depends(admin_required)])
def edit_role_form(request: Request, role_id: int):
    with SessionLocal() as db:
        role = db.get(Role, role_id)
        if role is None or role.is_admin:
            return staff_back(request, False, "The Administrator role can't be changed.", "/staff/roles")
    return RedirectResponse(url=f"/staff/roles?open={role_id}", status_code=303)


@app.post("/staff/roles/{role_id}/edit", dependencies=[Depends(admin_required)])
def save_role(request: Request, role_id: int, name: str = Form(""), perm: list[str] = Form([])):
    name, keys = clean_text(name, 40), perms.with_needs(perms.parse(",".join(perm)))
    with SessionLocal() as db:
        role = db.get(Role, role_id)
        if role is None or role.is_admin:
            return staff_back(request, False, "The Administrator role can't be changed.", "/staff/roles")
        error = role_form_error(db, name, keys, role.id)
        if error:
            return refused(request, role.id, name, keys, error)
        old_name, old_keys = role.name, perms.parse(role.permissions)
        role.name, role.permissions = name, perms.dump(keys)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return refused(request, role.id, name, keys, f'There is already a role called "{name}".')
    changes = []
    if name != old_name:
        changes.append(f'renamed from "{old_name}"')
    added = [perms.LABELS[k] for k in perms.KEYS if k in keys - old_keys]
    removed = [perms.LABELS[k] for k in perms.KEYS if k in old_keys - keys]
    if added:
        changes.append("added " + ", ".join(added))
    if removed:
        changes.append("removed " + ", ".join(removed))
    if not changes:
        return staff_back(request, True, "Nothing changed.", "/staff/roles")
    log_action("Staff", "Role Changed", f"{name}: " + "; ".join(changes), request)
    return staff_back(request, True, f"Saved {name}. People with this role see the change on their next click.", "/staff/roles")


@app.post("/staff/roles/{role_id}/delete", dependencies=[Depends(admin_required)])
def delete_role(request: Request, role_id: int):
    with SessionLocal() as db:
        role = db.get(Role, role_id)
        if role is None:
            return staff_back(request, True, "That role is already gone.", "/staff/roles")
        if role.is_admin:
            return staff_back(request, False, "The Administrator role can't be deleted.", "/staff/roles")
        people = db.query(Admin).filter(Admin.role_id == role.id).count()
        if people:
            return staff_back(request, False, f"{role.name} is still given to {people} "
                              f"{'account' if people == 1 else 'accounts'} (turned-off ones count too). "
                              "Give them another role first.", "/staff/roles")
        name = role.name
        db.delete(role)
        db.commit()
    log_action("Staff", "Role Deleted", f"Deleted the role {name}", request)
    return staff_back(request, True, f"Deleted the role {name}.", "/staff/roles")


# ---- First login: replace the temporary password, then get a personal recovery key
@app.get("/first-login")
def show_first_login(request: Request):
    check_signed_in(request)
    if not request.state.user.must_change:
        return RedirectResponse(url=home_url(request), status_code=303)
    return templates.TemplateResponse(request, "first_login.html", {"full_name": request.state.user.full_name})


@app.post("/first-login")
def process_first_login(request: Request, new_password: str = Form(""), confirm_password: str = Form("")):
    check_signed_in(request)
    me = request.state.user
    if not me.must_change:
        return RedirectResponse(url=home_url(request), status_code=303)
    with SessionLocal() as db:
        admin = db.get(Admin, me.id)
        error = validate_new_password(new_password, confirm_password)
        if not error and check_password(new_password, admin.password_hash):
            error = "Choose a password that is different from the temporary one."
        if error:
            return templates.TemplateResponse(request, "first_login.html",
                                              {"full_name": me.full_name, "error": error}, status_code=400)
        recovery_key = generate_recovery_key()
        admin.password_hash = hash_password(new_password)
        admin.recovery_key_hash = hash_password(recovery_key)
        admin.must_change_password = False
        db.commit()
        request.session["pwv"] = password_fingerprint(admin.password_hash)   # stays signed in
        me.must_change = False
    log_action("Security", "First Login Completed", "Chose their own password and received a recovery key", request)
    return templates.TemplateResponse(request, "recovery_key_display.html", {
        "recovery_key": recovery_key,
        "intro": "Your password is set. If you ever forget it, this key lets you choose a new one from the "
                 "login screen. Only you have it: the administrator can't see it. It will not be shown again.",
        "next_url": home_url(request), "next_label": "Continue to the app",
    })


# AUDIT LOGS
@app.get("/audit_logs", dependencies=[Depends(require("audit"))])
def show_audit_logs(
    request: Request,
    date_from: str = "",
    date_to: str = "",
    category: str = "",
    q: str = "",
    page: str = "1",
):
    # Same rule as Attendance: the date filter always has a value (today by default)
    d_from, d_to = resolve_date_range(date_from, date_to)
    if category not in LOG_CATEGORIES:
        category = ""

    db = SessionLocal()
    try:
        query = db.query(AdminLog).filter(AdminLog.created_at >= datetime.combine(d_from, time.min),
                                          AdminLog.created_at <= datetime.combine(d_to, time.max))

        if category:
            query = query.filter(AdminLog.category == category)
        if q:
            like = like_pattern(q)
            query = query.filter(AdminLog.admin_username.ilike(like, escape="\\")
                                 | AdminLog.action.ilike(like, escape="\\")
                                 | AdminLog.description.ilike(like, escape="\\"))

        total_records = query.count()
        page = parse_optional_int(page) or 1
        total_pages = max(1, math.ceil(total_records / PAGE_SIZE))
        page = max(1, min(page, total_pages))

        logs = (
            query.order_by(AdminLog.created_at.desc(), AdminLog.id.desc())
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
            .all()
        )

        response = templates.TemplateResponse(request, "audit_logs.html", {
            "logs": logs,
            "categories": LOG_CATEGORIES,
            "date_from": d_from.isoformat(),
            "date_to": d_to.isoformat(),
            "category": category,
            "search": q,
            "page": page,
            "total_pages": total_pages,
            "total_records": total_records,
            "start_record": (page - 1) * PAGE_SIZE + 1 if total_records else 0,
            "end_record": min(page * PAGE_SIZE, total_records),
        })
    finally:
        db.close()
    return response


# MAINTENANCE (backup + purge)
@app.get("/maintenance", dependencies=[Depends(require("maintenance"))])
def show_maintenance(request: Request):
    message = request.session.pop("flash", None)

    db_file = Path(data_path("barangay_system.db"))
    db_size_mb = round(db_file.stat().st_size / 1048576, 2) if db_file.exists() else 0

    db = SessionLocal()
    try:
        visit_count = db.query(Visit).count()
        oldest_visit = db.query(func.min(Visit.created_at)).scalar()
    finally:
        db.close()

    return templates.TemplateResponse(request, "maintenance.html", {
        "message": message,
        "db_size_mb": db_size_mb,
        "visit_count": visit_count,
        "oldest_visit": oldest_visit,
        "default_backup_dir": str(BACKUP_DIR_DEFAULT),
        "purge_min_days": PURGE_MIN_AGE_DAYS,
        "latest_cutoff": (date.today() - timedelta(days=PURGE_MIN_AGE_DAYS)).isoformat(),
        "printers": list_windows_printers(),
        "printer": load_printer_settings(),
        "dummy_printer": USE_DUMMY_PRINTER,
    })


@app.post("/maintenance/backup", dependencies=[Depends(require("maintenance"))])
def run_backup(request: Request, folder: str = Form("")):
    """Copy the live database to a timestamped file using SQLite's own backup API.

    (A plain file copy of a database that is being written to can produce a damaged copy.)
    """
    folder = folder.strip().strip('"')
    target_dir = Path(folder) if folder else BACKUP_DIR_DEFAULT
    try:
        if not folder:
            target_dir.mkdir(parents=True, exist_ok=True)
        elif not target_dir.is_absolute() or not target_dir.is_dir():
            raise FileNotFoundError(f"folder not found (use a full path such as D:\\Backups): {target_dir}")

        target = create_backup_file(target_dir, "barangay_system_")   # temp file + integrity check
    
    except Exception as e:
        flash(request, False, f"Backup failed: {e}")
        log_action("Database", "Backup Failed", f"Backup to {target_dir} failed: {e}", request)
    else:
        flash(request, True, f"Backup saved: {target}")
        log_action("Database", "Backup", f"Database backup saved to {target}", request)
    return RedirectResponse(url="/maintenance", status_code=303)

@app.post("/maintenance/printer/save", dependencies=[Depends(require("maintenance"))])
def save_printer(request: Request, printer_name: str = Form(""), paper_mm: str = Form("58")):
    name = clean_text(printer_name, 200)
    if paper_mm not in PAPER_COLUMNS:
        flash(request, False, "Choose 58 mm or 80 mm paper.")
    elif name and not USE_DUMMY_PRINTER and name not in list_windows_printers():
        flash(request, False, "That printer is not installed on this computer.")
    else:
        try:
            save_printer_settings(name, paper_mm)
        except OSError as e:
            log.exception("Saving printer settings failed")
            flash(request, False, f"Could not save the printer settings: {e}")
        else:
            flash(request, True, f'Receipt printer saved: {name or "none"} ({paper_mm} mm paper).')
            log_action("Settings", "Printer Changed", f'Receipt printer set to "{name or "none"}" ({paper_mm} mm paper)', request)
    return RedirectResponse(url="/maintenance", status_code=303)


@app.post("/maintenance/printer/test", dependencies=[Depends(require("maintenance"))])
def test_printer(request: Request):
    settings = load_printer_settings()
    try:
        deliver_to_printer(build_test_slip(PAPER_COLUMNS[settings["paper_mm"]]), settings)
    except Exception as e:
        log.exception("Test print failed")
        flash(request, False, f"Test print failed: {e}")
        log_action("Settings", "Print Error", f"Test print failed: {e}", request)
    else:
        flash(request, True, "Test slip sent to the printer. Check that it came out correctly.")
        log_action("Settings", "Test Print", "Printed a test slip", request)
    return RedirectResponse(url="/maintenance", status_code=303)

@app.post("/maintenance/purge", dependencies=[Depends(require("maintenance"))])
def purge_old_records(request: Request, cutoff: str = Form(...)):
    """Delete attendance history (visits) from before the cutoff date.

    Only the visits table is touched: never the live queue, services, accounts or the audit log.
    """
    cutoff_date = parse_date(cutoff)
    latest_allowed = date.today() - timedelta(days=PURGE_MIN_AGE_DAYS)

    if cutoff_date is None:
        flash(request, False, "Pick a valid date.")
    elif cutoff_date > latest_allowed:
        flash(request, False, f"For safety, only records older than {PURGE_MIN_AGE_DAYS} days can be purged "
                              f"(latest allowed date: {latest_allowed.strftime('%b %d, %Y')}).")
    else:
        try:
            BACKUP_DIR_DEFAULT.mkdir(parents=True, exist_ok=True)
            create_backup_file(BACKUP_DIR_DEFAULT, "prepurge_")
        except Exception as e:
            flash(request, False, f"Purge cancelled: a safety backup could not be made first ({e}).")
            return RedirectResponse(url="/maintenance", status_code=303)
        
        db = SessionLocal()
        try:
            deleted = db.query(Visit).filter(
                Visit.created_at < datetime.combine(cutoff_date, time.min)
            ).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()

        if deleted:
            try:   # hand the freed space back to the disk
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                    conn.execute(text("VACUUM"))
            except Exception:
                log.exception("VACUUM failed")
            log_action("Database", "Data Purge",
                       f"Purged {deleted} attendance record(s) from before {cutoff_date.strftime('%b %d, %Y')}", request)
            flash(request, True, f"Purged {deleted} attendance record(s) from before {cutoff_date.strftime('%b %d, %Y')}.")
        else:
            flash(request, True, f"No attendance records exist from before {cutoff_date.strftime('%b %d, %Y')}. Nothing was deleted.")
    return RedirectResponse(url="/maintenance", status_code=303)

# SETTINGS (barangay profile, logos, signatories, document defaults) and the FIRST-RUN WIZARD.
# The wizard and the Settings page share the same helpers, so the same rules apply in both places.
SETTING_LABELS = {
    "barangay_name": "Barangay name", "municipality": "Municipality", "province": "Province",
    "header_line_1": "Header line 1", "header_line_2": "Office line", "office_color": "Office line colour",
    "signatory_captain_name": "Captain name", "signatory_captain_title": "Captain title",
    "signatory_secretary_name": "Secretary name", "signatory_secretary_title": "Secretary title",
    "control_number_format": "Control number style",
}

def profile_values(barangay_name, municipality, province, header_line_1, header_line_2, office_color) -> dict:
    return {"barangay_name": clean_text(barangay_name, 60), "municipality": clean_text(municipality, 60),
            "province": clean_text(province, 60), "header_line_1": clean_text(header_line_1, 80),
            "header_line_2": clean_text(header_line_2, 80), "office_color": branding.clean_color(office_color)}

def signatory_values(captain_name, captain_title, secretary_name, secretary_title) -> dict:
    return {"signatory_captain_name": clean_text(captain_name, 80),
            "signatory_captain_title": clean_text(captain_title, 60),
            "signatory_secretary_name": clean_text(secretary_name, 80),
            "signatory_secretary_title": clean_text(secretary_title, 60)}

def logo_changes_from_form(files: dict, remove_flags: dict):
    """Which logo pictures were chosen and which slots were emptied, read from the submitted form."""
    uploads, removals = {}, set()
    for slot in branding.LOGO_SLOTS:
        chosen = files[slot]
        if chosen is not None and chosen.filename:
            uploads[slot] = chosen.file.read(branding.MAX_LOGO_BYTES + 1)   # one byte over the limit is enough to refuse it
        elif remove_flags[slot] == "1":
            removals.add(slot)
    return uploads, removals

def apply_settings(request: Request, new: dict, action: str) -> bool:
    """Save the settings that changed and write ONE audit line describing them. True if anything changed."""
    old = branding.load_settings()
    changes = [f'{SETTING_LABELS[k]}: "{old[k]}" -> "{v}"' for k, v in new.items() if old[k] != v]
    if changes:
        branding.save_settings(new)
        log_action("Settings", action, "; ".join(changes), request)
    return bool(changes)

def apply_logos(request: Request, uploads: dict, removals: set) -> list:
    """Raises branding.LogoError (and changes nothing) if a picture is refused."""
    changes = branding.apply_logo_changes(uploads, removals)
    if changes:
        log_action("Settings", "Logos Updated", "; ".join(changes), request)
    branding.clear_later("logos")        # she has looked at this section, so stop reminding her
    return changes

# ---- the Settings page
def settings_page(request: Request, form: dict | None = None, errors: dict | None = None, status_code: int = 200):
    """Draw the Settings page. `form` is what was typed in a section that failed (so it is not lost);
    `errors` is {section: message}, shown inside that section."""
    values = branding.load_settings()
    return templates.TemplateResponse(request, "settings.html", {
        "values": values,                                # what is saved
        "shown": {**values, **(form or {})},             # what the boxes show
        "errors": errors or {},
        "saved": request.session.pop("settings_flash", None),   # one-time "Saved." message
        "logos": branding.logo_info(values),
        "control_formats": branding.CONTROL_FORMATS,
        "office_colors": branding.OFFICE_COLORS,
    }, status_code=status_code)

def settings_saved(request: Request, section: str, changed: bool):
    request.session["settings_flash"] = {"section": section, "ok": True,
                                         "text": "Saved." if changed else "Nothing was changed."}
    return RedirectResponse(url=f"/settings#{section}", status_code=303)

@app.get("/settings", dependencies=[Depends(require("settings"))])
def show_settings(request: Request):
    return settings_page(request)

@app.post("/settings/profile", dependencies=[Depends(require("settings"))])
def save_settings_profile(request: Request, barangay_name: str = Form(""), municipality: str = Form(""),
                          province: str = Form(""), header_line_1: str = Form(""), header_line_2: str = Form(""),
                          office_color: str = Form("")):
    new = profile_values(barangay_name, municipality, province, header_line_1, header_line_2, office_color)
    error = branding.validate_profile(new)
    if error:
        return settings_page(request, form=new, errors={"profile": error}, status_code=400)
    return settings_saved(request, "profile", apply_settings(request, new, "Profile Updated"))

@app.post("/settings/signatories", dependencies=[Depends(require("settings"))])
def save_settings_signatories(request: Request, signatory_captain_name: str = Form(""), signatory_captain_title: str = Form(""),
                              signatory_secretary_name: str = Form(""), signatory_secretary_title: str = Form("")):
    new = signatory_values(signatory_captain_name, signatory_captain_title,
                           signatory_secretary_name, signatory_secretary_title)
    error = branding.validate_signatories(new)
    if error:
        return settings_page(request, form=new, errors={"signatories": error}, status_code=400)
    return settings_saved(request, "signatories", apply_settings(request, new, "Signatories Updated"))

@app.post("/settings/documents", dependencies=[Depends(require("settings"))])
def save_settings_documents(request: Request, control_number_format: str = Form("")):
    new = {"control_number_format": control_number_format.strip()}
    error = branding.validate_documents(new)
    if error:
        return settings_page(request, form=new, errors={"documents": error}, status_code=400)
    return settings_saved(request, "documents", apply_settings(request, new, "Document Defaults Updated"))

@app.post("/settings/logos", dependencies=[Depends(require("settings"))])
def save_settings_logos(request: Request,
                        logo_left: UploadFile | None = File(None), logo_right: UploadFile | None = File(None),
                        logo_third: UploadFile | None = File(None),
                        remove_left: str = Form("0"), remove_right: str = Form("0"), remove_third: str = Form("0")):
    uploads, removals = logo_changes_from_form(
        {"left": logo_left, "right": logo_right, "third": logo_third},
        {"left": remove_left, "right": remove_right, "third": remove_third})
    try:
        changes = apply_logos(request, uploads, removals)
    except branding.LogoError as e:
        return settings_page(request, errors={"logos": str(e)}, status_code=400)   # nothing was changed
    return settings_saved(request, "logos", bool(changes))

@app.get("/branding/{slot}.png")
def branding_logo(slot: str):
    """The saved logos (kept inside the database since step 8b). Open to everyone on purpose (so the
    sign-in screens can show them); the server only listens on this computer. Serves only the three
    known slot names, never a picture chosen by number."""
    picture_id = branding.logo_picture_id(slot) if slot in branding.LOGO_SLOTS else None
    found = pictures.load(picture_id) if picture_id else None
    if found is None:
        return Response(status_code=404)
    data, mime = found
    return Response(data, media_type=mime,
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})   # the ?v= in the URL changes on every upload

@app.get("/pictures/{picture_id}", dependencies=[Depends(login_required)])
def design_picture(picture_id: int):
    """A design picture (banner, watermark) by its number. A stored picture never changes, so the
    browser may keep it for good."""
    found = pictures.load(picture_id)
    if found is None:
        return Response(status_code=404)
    data, mime = found
    return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=31536000, immutable"})

branding.seed_legacy_logos()     # acts once, only on an installation upgraded from the Barangay 4 edition

# ---- the first-run wizard: three steps after the first sign-in, then a finish screen
def wizard_gate(step: str):
    """None if she may be on this step; otherwise where to send her. Finished setup goes to the
    dashboard; she can go Back to an earlier step but not jump ahead of her progress."""
    if branding.is_setup_complete():
        return RedirectResponse(url="/dashboard", status_code=303)
    order = branding.WIZARD_STEPS
    current = branding.wizard_step()
    if order.index(step) > order.index(current):
        return RedirectResponse(url=f"/wizard/{current}", status_code=303)
    return None

def wizard_page(request: Request, step: str, form: dict | None = None, error: str | None = None, status_code: int = 200):
    values = branding.load_settings()
    logos = branding.logo_info(values)
    skipped = []                         # what the finish screen reminds her about
    if "logos" in branding.later_items(values) and not any(info["url"] for info in logos.values()):
        skipped.append("logos")
    if not (values["signatory_captain_name"] and values["signatory_secretary_name"]):
        skipped.append("signatories")
    return templates.TemplateResponse(request, "wizard.html", {
        "step": step,
        "step_number": branding.WIZARD_STEPS.index(step) + 1,
        "step_index": branding.WIZARD_STEPS.index,
        "shown": {**values, **(form or {})},
        "logos": logos,
        "skipped": skipped,
        "error": error,
        "office_colors": branding.OFFICE_COLORS,
    }, status_code=status_code)

@app.get("/wizard", dependencies=[Depends(wizard_login)])
def wizard_start():
    if branding.is_setup_complete():
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url=f"/wizard/{branding.wizard_step()}", status_code=303)

@app.get("/wizard/test-document", dependencies=[Depends(wizard_login)])
def wizard_test_document(request: Request):
        return templates.TemplateResponse(request, "test_document.html", {})

@app.get("/wizard/{step}", dependencies=[Depends(wizard_login)])
def show_wizard_step(request: Request, step: str):
    if step not in branding.WIZARD_STEPS:
        return RedirectResponse(url="/wizard", status_code=303)
    return wizard_gate(step) or wizard_page(request, step)

@app.post("/wizard/profile", dependencies=[Depends(wizard_login)])
def save_wizard_profile(request: Request, barangay_name: str = Form(""), municipality: str = Form(""),
                        province: str = Form(""), header_line_1: str = Form(""), header_line_2: str = Form(""),
                        office_color: str = Form("")):
    blocked = wizard_gate("profile")
    if blocked:
        return blocked
    new = profile_values(barangay_name, municipality, province, header_line_1, header_line_2, office_color)
    error = branding.validate_profile(new)
    if error:
        return wizard_page(request, "profile", form=new, error=error, status_code=400)
    apply_settings(request, new, "Profile Updated")
    branding.advance_wizard("logos")
    return RedirectResponse(url="/wizard/logos", status_code=303)

@app.post("/wizard/logos", dependencies=[Depends(wizard_login)])
def save_wizard_logos(request: Request,
                      logo_left: UploadFile | None = File(None), logo_right: UploadFile | None = File(None),
                      logo_third: UploadFile | None = File(None),
                      remove_left: str = Form("0"), remove_right: str = Form("0"), remove_third: str = Form("0")):
    blocked = wizard_gate("logos")
    if blocked:
        return blocked
    uploads, removals = logo_changes_from_form(
        {"left": logo_left, "right": logo_right, "third": logo_third},
        {"left": remove_left, "right": remove_right, "third": remove_third})
    already_there = {s for s, info in branding.logo_info(branding.load_settings()).items() if info["url"]}
    if not uploads and not (already_there - removals):
        return wizard_page(request, "logos", status_code=400,
                           error='Choose at least one picture, or press "Do this later".')
    try:
        apply_logos(request, uploads, removals)
    except branding.LogoError as e:
        return wizard_page(request, "logos", error=str(e), status_code=400)   # nothing was changed
    branding.advance_wizard("signatories")
    return RedirectResponse(url="/wizard/signatories", status_code=303)

@app.post("/wizard/logos/later", dependencies=[Depends(wizard_login)])
def skip_wizard_logos(request: Request):
    blocked = wizard_gate("logos")
    if blocked:
        return blocked
    branding.mark_later("logos")
    branding.advance_wizard("signatories")
    return RedirectResponse(url="/wizard/signatories", status_code=303)

@app.post("/wizard/signatories", dependencies=[Depends(wizard_login)])
def save_wizard_signatories(request: Request, signatory_captain_name: str = Form(""), signatory_captain_title: str = Form(""),
                            signatory_secretary_name: str = Form(""), signatory_secretary_title: str = Form("")):
    blocked = wizard_gate("signatories")
    if blocked:
        return blocked
    new = signatory_values(signatory_captain_name, signatory_captain_title,
                           signatory_secretary_name, signatory_secretary_title)
    error = branding.validate_signatories(new, require_names=True)
    if error:
        return wizard_page(request, "signatories", form=new, error=error, status_code=400)
    apply_settings(request, new, "Signatories Updated")
    branding.advance_wizard("finish")
    return RedirectResponse(url="/wizard/finish", status_code=303)

@app.post("/wizard/signatories/later", dependencies=[Depends(wizard_login)])
def skip_wizard_signatories(request: Request):
    blocked = wizard_gate("signatories")
    if blocked:
        return blocked
    branding.advance_wizard("finish")
    return RedirectResponse(url="/wizard/finish", status_code=303)

@app.post("/wizard/finish", dependencies=[Depends(wizard_login)])
def finish_wizard(request: Request):
    blocked = wizard_gate("finish")
    if blocked:
        return blocked
    branding.finish_setup()
    log_action("Settings", "Setup Completed", "First-run setup finished", request)
    return RedirectResponse(url="/dashboard", status_code=303)

# DOCUMENTS: THE TEMPLATES TAB (the wording of each kind of document)
def template_name_error(db, name: str, exclude_id: int | None = None) -> str | None:
    if not name:
        return "The document needs a name."
    q = db.query(DocumentTemplate).filter(func.lower(DocumentTemplate.name) == name.lower())
    if exclude_id:
        q = q.filter(DocumentTemplate.id != exclude_id)
    return f'A document named "{name}" already exists.' if q.first() else None

def unique_template_name(db, base: str) -> str:
    """'Barangay Clearance (copy)', then '(copy 2)', '(copy 3)'..."""
    base = base[:48]
    candidate, n = f"{base} (copy)", 2
    while template_name_error(db, candidate):
        candidate, n = f"{base} (copy {n})", n + 1
    return candidate

def unlink_services(db, template_id: int) -> list:
    """Services that point at this template stop pointing at it. Returns their names (the caller commits)."""
    linked = db.query(Service).filter(Service.document_template_id == template_id).all()
    for service in linked:
        service.document_template_id = None
    return [service.service_name for service in linked]

def effective_body(row) -> str:
    """The saved wording, or the built-in wording from the code when none is saved."""
    return doc_templates.normalize_body(row.body if row.body is not None else doc_templates.builtin_body(row.builtin_key))

def template_views(db) -> list:
    linked = {}
    for s in db.query(Service).filter(Service.document_template_id.isnot(None)).order_by(Service.service_name).all():
        linked.setdefault(s.document_template_id, []).append(s.service_name)
    return [{
        "id": r.id, "name": r.name, "builtin": r.builtin_key is not None,
        "customized": r.builtin_key is not None and r.body is not None,
        "code_prefix": r.code_prefix,
        "fee_text": doc_templates.format_pesos(r.default_fee_centavos) if r.default_fee_centavos else "No fee",
        "services": linked.get(r.id, []), "active": bool(r.is_active),
    } for r in db.query(DocumentTemplate).order_by(DocumentTemplate.id).all()]

@app.get("/documents/templates", dependencies=[Depends(require("templates"))])
def show_templates(request: Request):
    message = request.session.pop("flash", None)
    with SessionLocal() as db:
        items = template_views(db)
    return templates.TemplateResponse(request, "documents_templates.html", {"items": items, "message": message})

# ---- the editor
def editor_values(row) -> dict:
    return {"name": row.name, "body": effective_body(row), "code_prefix": row.code_prefix,
            "default_fee": doc_templates.format_fee_input(row.default_fee_centavos),
            "use_validity": row.validity_months is not None, "validity_months": row.validity_months or 6,
            "extras": [e for e in row.extras.split(",") if e], "print_fee": bool(row.print_fee),
            "auto_bold": bool(row.auto_bold), "signers": row.signers,
            "design": design_of(row), "font_half_pt": row.font_half_pt,
            "line_spacing": row.line_spacing, "para_gap": row.para_gap, "sign_space_mm": row.sign_space_mm}

def look_of(row) -> dict:
    """How a template's wording is spaced (step 8c), and the space above the signatures (step 8d).
    The automatic indent is gone: indents come from Tab."""
    return documents.look(row.font_half_pt, row.line_spacing, row.para_gap, row.sign_space_mm)

def design_of(row):
    """The design of a template (a dict), or None for a plain page. It was checked when it was saved."""
    if not row.design_json:
        return None
    try:
        return documents.parse_design(row.design_json)
    except ValueError:
        log.exception("The saved design of template %s could not be read; printing a plain page", row.id)
        return None

def other_designs(db, template_id: int) -> dict:
    """For "Copy the design from": every other template that has a design."""
    return {r.id: {"name": r.name, "design": design_of(r)}
            for r in db.query(DocumentTemplate).filter(DocumentTemplate.id != template_id,
                                                       DocumentTemplate.design_json.isnot(None)).order_by(DocumentTemplate.id)
            if design_of(r)}

def signer_options(b: dict) -> dict:
    """The choices for "who signs", worded with the titles from Settings."""
    return {"both": f'{b["secretary_title"]} and {b["captain_title"]}',
            "captain": f'{b["captain_title"]} only', "secretary": f'{b["secretary_title"]} only'}

def render_template_editor(request: Request, row, form: dict | None = None, error: str | None = None, status_code: int = 200):
    """`form` is what was typed when a save failed, so nothing is lost."""
    shown = {**editor_values(row), **(form or {})}
    with SessionLocal() as db:
        designs = other_designs(db, row.id)
    return templates.TemplateResponse(request, "template_editor.html", {
        "t": {"id": row.id, "builtin": row.builtin_key is not None,
              "customized": row.builtin_key is not None and row.body is not None},
        "shown": shown, "error": error, "message": request.session.pop("flash", None),
        "core_fields": doc_templates.CORE_FIELDS, "extra_menu": doc_templates.EXTRA_MENU,
        "validity_field": doc_templates.VALIDITY_FIELD,
        "available": doc_templates.available_fields(shown["extras"], shown["use_validity"]),
        "signer_options": signer_options(branding.get_branding()),
        "designs": designs, "design_mm": documents.DESIGN_MM, "layers": documents.LAYERS,
        "font_sizes": documents.FONT_SIZES, "print_design": documents.for_print(shown["design"]),
        "line_spacings": documents.LINE_SPACINGS, "para_gaps": documents.PARA_GAPS,
        "look": documents.look(shown["font_half_pt"], shown["line_spacing"], shown["para_gap"], shown["sign_space_mm"]),
        "sign_mm": documents.SIGN_MM,
    }, status_code=status_code)

def read_template_form(db, template_id, name, body, code_prefix, default_fee, use_validity, validity_months, extras,
                       print_fee, auto_bold, signers, design_text: str, font_half_pt: str, line_spacing: str,
                       para_gap: str, sign_space_mm: str):
    """Returns (values to save, what she typed, error). Exactly one of values / error is None."""
    typed = {"name": clean_text(name, 60), "body": doc_templates.normalize_body(body),
             "code_prefix": code_prefix.strip().upper(), "default_fee": default_fee.strip(),
             "use_validity": use_validity == "1", "validity_months": validity_months.strip() or "6",
             "extras": doc_templates.clean_extras(extras), "print_fee": print_fee == "1",
             "auto_bold": auto_bold == "1",
             "line_spacing": int(line_spacing) if line_spacing.strip().isdigit() and int(line_spacing) in documents.LINE_SPACINGS else 115,
             "para_gap": para_gap if para_gap in documents.PARA_GAPS else "small",
             "sign_space_mm": (int(sign_space_mm) if sign_space_mm.strip().isdigit()
                               and documents.SIGN_MM[0] <= int(sign_space_mm) <= documents.SIGN_MM[1]
                               else documents.DEFAULT_SIGN_MM),
             "signers": signers if signers in doc_templates.SIGNER_CHOICES else "both",
             "design": None, "font_half_pt": documents.DEFAULT_FONT}
    try:                                                       # shown again as it was if the save fails
        typed["design"] = documents.parse_design(design_text)
    except ValueError:
        pass
    if font_half_pt.strip().isdigit() and int(font_half_pt) in documents.FONT_SIZES:
        typed["font_half_pt"] = int(font_half_pt)
    try:
        problem = template_name_error(db, typed["name"], template_id)
        if problem:
            raise ValueError(problem)
        design = documents.parse_design(design_text)
        chosen = documents.pictures_of(design)
        if set(chosen) - pictures.existing(db, chosen):
            raise ValueError("A picture of the design could not be found. Please load the design again.")
        font = documents.parse_font(font_half_pt)
        spacing = documents.parse_spacing(line_spacing, para_gap)
        sign_space = documents.parse_sign_space(sign_space_mm)
        prefix = doc_templates.parse_prefix(typed["code_prefix"])
        fee = doc_templates.parse_fee(typed["default_fee"])
        months = doc_templates.parse_months(typed["validity_months"]) if typed["use_validity"] else None
    except ValueError as e:
        return None, typed, str(e)
    problem = doc_templates.validate_body(
        typed["body"], doc_templates.available_fields(typed["extras"], typed["use_validity"]))
    if problem:
        return None, typed, problem
    return {"name": typed["name"], "body": typed["body"], "code_prefix": prefix, "default_fee_centavos": fee,
            "validity_months": months, "extras": ",".join(typed["extras"]), "print_fee": typed["print_fee"],
            "auto_bold": typed["auto_bold"], "signers": typed["signers"],
            "design": design, "font_half_pt": font, "line_spacing": spacing[0], "para_gap": spacing[1],
            "sign_space_mm": sign_space}, typed, None

@app.get("/documents/templates/{template_id}/edit", dependencies=[Depends(require("templates"))])
def edit_template_page(request: Request, template_id: int):
    with SessionLocal() as db:
        row = db.get(DocumentTemplate, template_id)
        if row is None:
            return RedirectResponse(url="/documents/templates", status_code=303)
        return render_template_editor(request, row)

@app.post("/documents/templates/{template_id}/edit", dependencies=[Depends(require("templates"))])
def save_template(request: Request, template_id: int, name: str = Form(""), body: str = Form(""),
                  code_prefix: str = Form(""), default_fee: str = Form(""), use_validity: str = Form(""),
                  validity_months: str = Form(""), extras: list[str] = Form(default=[]), print_fee: str = Form(""),
                  auto_bold: str = Form(""), signers: str = Form("both"),
                  design: str = Form(""), font_half_pt: str = Form("21"), line_spacing: str = Form("115"),
                  para_gap: str = Form("small"), sign_space_mm: str = Form("27")):
    with SessionLocal() as db:
        row = db.get(DocumentTemplate, template_id)
        if row is None:
            return RedirectResponse(url="/documents/templates", status_code=303)
        values, typed, error = read_template_form(db, row.id, name, body, code_prefix, default_fee,
                                                  use_validity, validity_months, extras, print_fee,
                                                  auto_bold, signers, design, font_half_pt, line_spacing, para_gap,
                                                  sign_space_mm)
        if error:
            return render_template_editor(request, row, form=typed, error=error, status_code=400)

        changes = []
        if row.name != values["name"]:
            changes.append(f'renamed from "{row.name}"')
        if effective_body(row) != values["body"]:
            changes.append("wording changed")
        if row.code_prefix != values["code_prefix"]:
            changes.append(f'prefix {row.code_prefix} -> {values["code_prefix"]}')
        if row.default_fee_centavos != values["default_fee_centavos"]:
            changes.append(f"fee {doc_templates.format_pesos(row.default_fee_centavos)} -> "
                           f"{doc_templates.format_pesos(values['default_fee_centavos'])}")
        if row.validity_months != values["validity_months"]:
            changes.append(f"validity {row.validity_months or 'none'} -> {values['validity_months'] or 'none'} months")
        if row.extras != values["extras"]:
            changes.append(f"extra fields \"{row.extras or 'none'}\" -> \"{values['extras'] or 'none'}\"")
        if bool(row.print_fee) != values["print_fee"]:
            changes.append("print fee " + ("on" if values["print_fee"] else "off"))
        if bool(row.auto_bold) != values["auto_bold"]:
            changes.append("automatic bold " + ("on" if values["auto_bold"] else "off"))
        if row.line_spacing != values["line_spacing"]:
            changes.append(f'line spacing {documents.LINE_SPACINGS.get(row.line_spacing, "?")} -> '
                           f'{documents.LINE_SPACINGS[values["line_spacing"]]}')
        if row.para_gap != values["para_gap"]:
            changes.append(f'space after paragraphs {documents.PARA_GAPS[values["para_gap"]][0].lower()}')
        if row.sign_space_mm != values["sign_space_mm"]:
            changes.append(f'space above signatures {row.sign_space_mm} -> {values["sign_space_mm"]} mm')
        if row.signers != values["signers"]:
            changes.append(f'signed by {row.signers} -> {values["signers"]}')
        design_changed = documents.design_changes(design_of(row), values["design"])
        if design_changed:
            changes.append("design: " + ", ".join(design_changed))
        if row.font_half_pt != values["font_half_pt"]:
            changes.append(f'text size {documents.FONT_SIZES.get(row.font_half_pt, "?")} -> '
                           f'{documents.FONT_SIZES[values["font_half_pt"]]} pt')

        # A built-in whose wording is back to the original keeps following the code (nothing saved)
        same_as_original = (row.builtin_key is not None
                            and values["body"] == doc_templates.normalize_body(doc_templates.builtin_body(row.builtin_key)))
        row.name, row.code_prefix = values["name"], values["code_prefix"]
        row.body = None if same_as_original else values["body"]
        row.default_fee_centavos, row.validity_months = values["default_fee_centavos"], values["validity_months"]
        row.extras, row.print_fee = values["extras"], values["print_fee"]
        row.auto_bold, row.signers = values["auto_bold"], values["signers"]
        row.line_spacing, row.para_gap = values["line_spacing"], values["para_gap"]
        row.sign_space_mm = values["sign_space_mm"]
        row.design_json = documents.to_json(values["design"]) if values["design"] else None
        row.font_half_pt = values["font_half_pt"]
        db.commit()
        saved_name = values["name"]
    if changes:
        log_action("Documents", "Template Updated", f'Template "{saved_name}": ' + "; ".join(changes), request)
    flash(request, True, f'"{saved_name}" was saved.' if changes else "Nothing was changed.")
    return RedirectResponse(url="/documents/templates", status_code=303)

@app.post("/documents/templates/preview", dependencies=[Depends(require("templates"))])
def preview_template(body: str = Form(""), default_fee: str = Form(""), use_validity: str = Form(""),
                     validity_months: str = Form(""), extras: list[str] = Form(default=[]),
                     code_prefix: str = Form(""), auto_bold: str = Form("")):
    """The live preview in the editor: the same code that prints, filled with made-up client details.
    Never raises on half-typed input; it just reports what is wrong so far."""
    extras = doc_templates.clean_extras(extras)
    validity_on = use_validity == "1"
    try:
        fee = doc_templates.parse_fee(default_fee)
    except ValueError:
        fee = 0
    try:
        months = doc_templates.parse_months(validity_months) if validity_on else None
    except ValueError:
        months = None
    text = doc_templates.normalize_body(body)
    error = doc_templates.validate_body(text, doc_templates.available_fields(extras, validity_on))
    b = branding.get_branding()
    try:
        prefix = doc_templates.parse_prefix(code_prefix)
    except ValueError:
        prefix = "DOC"
    example = branding.format_control_number(b["control_number_format"], prefix, date.today().year, 1)
    values = doc_templates.sample_values(b, fee, months, date.today(), control_number=example)
    return JSONResponse({"paragraphs": doc_templates.render_paragraphs(text, values, auto_bold == "1"),
                         "error": error, "has_number": doc_templates.has_control_number(text)})

# ---- loading a design: from a Word file, or from a picture of the whole blank page. The pictures are
# stored at once (so the preview can show them); the template itself only changes on Save template.
@app.post("/documents/designs/import", dependencies=[Depends(require("templates"))])
def import_design(request: Request, design_file: UploadFile | None = File(None), kind: str = Form("")):
    if kind not in ("docx", "picture") or design_file is None or not design_file.filename:
        return JSONResponse({"error": "Please choose a file."}, status_code=400)
    file_name = clean_text(design_file.filename, 120)
    limit = docx_design.MAX_FILE_BYTES if kind == "docx" else pictures.DESIGN_MAX_BYTES
    try:
        design, notes = docx_design.build_design(kind, design_file.file.read(limit + 1), file_name, pictures.store)
        design = documents.parse_design(documents.to_json(design))      # the same checks as on Save
    except (docx_design.DocxError, pictures.PictureError, ValueError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    log.info("Design loaded from %s %r: %s picture(s)", kind, file_name, len(design["items"]))
    return JSONResponse({"design": design, "notes": notes})

# ---- restore, duplicate, new, turn on / off, delete
@app.post("/documents/templates/{template_id}/restore", dependencies=[Depends(require("templates"))])
def restore_template(request: Request, template_id: int):
    """Built-in documents only: go back to the wording in the code. Other settings are untouched."""
    with SessionLocal() as db:
        row = db.get(DocumentTemplate, template_id)
        if row is None or row.builtin_key is None:
            return RedirectResponse(url="/documents/templates", status_code=303)
        name = row.name
        changed = row.body is not None
        row.body = None
        db.commit()
    if changed:
        log_action("Documents", "Template Restored", f'Restored the original wording of "{name}"', request)
    flash(request, True, "The original wording was restored." if changed else "The wording was already the original.")
    return RedirectResponse(url=f"/documents/templates/{template_id}/edit", status_code=303)

@app.post("/documents/templates/{template_id}/duplicate", dependencies=[Depends(require("templates"))])
def duplicate_template(request: Request, template_id: int):
    with SessionLocal() as db:
        row = db.get(DocumentTemplate, template_id)
        if row is None:
            return RedirectResponse(url="/documents/templates", status_code=303)
        copy = DocumentTemplate(
            builtin_key=None, name=unique_template_name(db, row.name), body=effective_body(row),
            code_prefix=row.code_prefix, extras=row.extras, default_fee_centavos=row.default_fee_centavos,
            validity_months=row.validity_months, print_fee=row.print_fee, is_active=True,
            auto_bold=row.auto_bold, auto_indent=row.auto_indent, signers=row.signers,
            line_spacing=row.line_spacing, para_gap=row.para_gap, sign_space_mm=row.sign_space_mm)
        copy.design_json, copy.font_half_pt = row.design_json, row.font_half_pt
        db.add(copy)
        db.commit()
        copy_id, copy_name, original = copy.id, copy.name, row.name
    log_action("Documents", "Template Duplicated", f'Copied "{original}" as "{copy_name}"', request)
    flash(request, True, f'Copied as "{copy_name}". Change its name and wording below.')
    return RedirectResponse(url=f"/documents/templates/{copy_id}/edit", status_code=303)

@app.post("/documents/templates/new", dependencies=[Depends(require("templates"))])
def new_template(request: Request):
    """A new template starts from the Blank document's wording."""
    blank = doc_templates.BUILTINS["blank_en"]
    with SessionLocal() as db:
        name, n = "New document", 2
        while template_name_error(db, name):
            name, n = f"New document {n}", n + 1
        created = DocumentTemplate(builtin_key=None, name=name, body=doc_templates.normalize_body(blank["body"]),
                                   code_prefix=blank["prefix"], extras="", default_fee_centavos=0,
                                   validity_months=None, print_fee=True, is_active=True)
        db.add(created)
        db.commit()
        created_id = created.id
    log_action("Documents", "Template Created", f'Created a new template "{name}"', request)
    flash(request, True, "New template created. Give it a name and write its wording below.")
    return RedirectResponse(url=f"/documents/templates/{created_id}/edit", status_code=303)

@app.post("/documents/templates/{template_id}/deactivate", dependencies=[Depends(require("templates"))])
def deactivate_template(request: Request, template_id: int):
    with SessionLocal() as db:
        row = db.get(DocumentTemplate, template_id)
        if row is None:
            return RedirectResponse(url="/documents/templates", status_code=303)
        name = row.name
        unlinked = unlink_services(db, row.id)
        row.is_active = False
        db.commit()
    note = f"; removed from services: {', '.join(unlinked)}" if unlinked else ""
    log_action("Documents", "Template Turned Off", f'Turned off "{name}"{note}', request)
    return RedirectResponse(url="/documents/templates", status_code=303)

@app.post("/documents/templates/{template_id}/activate", dependencies=[Depends(require("templates"))])
def activate_template(request: Request, template_id: int):
    with SessionLocal() as db:
        row = db.get(DocumentTemplate, template_id)
        if row is None:
            return RedirectResponse(url="/documents/templates", status_code=303)
        name = row.name
        row.is_active = True
        db.commit()
    log_action("Documents", "Template Turned On", f'Turned on "{name}"', request)
    return RedirectResponse(url="/documents/templates", status_code=303)

@app.post("/documents/templates/{template_id}/delete", dependencies=[Depends(require("templates"))])
def delete_template(request: Request, template_id: int):
    """Custom templates only. One that already has issued documents is turned off instead, like services."""
    entry = None
    with SessionLocal() as db:
        row = db.get(DocumentTemplate, template_id)
        if row is None:
            return RedirectResponse(url="/documents/templates", status_code=303)
        if row.builtin_key is not None:
            flash(request, False, "Built-in documents cannot be deleted. Turn it off instead.")
            return RedirectResponse(url="/documents/templates", status_code=303)
        name = row.name
        has_history = db.query(IssuedDocument).filter(IssuedDocument.template_id == template_id).first() is not None
        unlinked = unlink_services(db, template_id)
        note = f"; removed from services: {', '.join(unlinked)}" if unlinked else ""
        if has_history:
            row.is_active = False
            db.commit()
            entry = ("Template Turned Off", f'"{name}" was deleted, but documents were issued with it, so it was turned off instead{note}')
        else:
            try:
                db.delete(row)
                db.commit()
                entry = ("Template Deleted", f'Deleted template "{name}"{note}')
            except IntegrityError:          # a document was issued a moment ago: keep the row
                db.rollback()
                row = db.get(DocumentTemplate, template_id)
                unlink_services(db, template_id)
                row.is_active = False
                db.commit()
                entry = ("Template Turned Off", f'"{name}" gained issued documents, so it was turned off instead')
    log_action("Documents", entry[0], entry[1], request)
    return RedirectResponse(url="/documents/templates", status_code=303)

# DOCUMENTS: ISSUING (the picker, the form, the print page) AND THE HISTORY
def signatories_missing(b: dict) -> bool:
    """True when a signatory name is not set. Only the documents signed by that person are blocked
    (see documents.missing_signers); the picker just warns."""
    return not (b["captain_name"] and b["secretary_name"])

def carry_params(client_name: str, address: str, ticket_number: str, ticket_date: str) -> dict:
    """What 'Choose another document' keeps. Cleaned again, because it came from the address bar."""
    carry = {"client_name": clean_text(client_name, 100), "address": clean_text(address, 150)}
    if TICKET_RE.match(ticket_number or "") and parse_date(ticket_date):
        carry["ticket_number"], carry["ticket_date"] = ticket_number, ticket_date
    return {k: v for k, v in carry.items() if v}

def issue_control_number(db, prefix: str, year: int, style: str) -> str:
    """Next control number, like BC-2026-0001. Same upsert as ticket numbers: it takes SQLite's write
    lock, held until the document is saved, so two people never get one number, and a failed save
    rolls the number back (no gaps)."""
    n = db.execute(text("""
        INSERT INTO document_counters (prefix, year, last_number) VALUES (:prefix, :year, 1)
        ON CONFLICT (prefix, year) DO UPDATE SET last_number = last_number + 1
        RETURNING last_number"""), {"prefix": prefix, "year": year}).scalar_one()
    return branding.format_control_number(style, prefix, year, n)

def safe_back(back: str) -> str:
    """After voiding or adding an OR number she returns to her filtered list, never to another site."""
    return back if back.startswith("/documents/history") else "/documents/history"

# ---- the picker (the "Issue a document" tab)
@app.get("/documents", dependencies=[Depends(require("issue"))])
def documents_picker(request: Request, client_name: str = "", address: str = "",
                     ticket_number: str = "", ticket_date: str = ""):
    carry = carry_params(client_name, address, ticket_number, ticket_date)
    message = request.session.pop("flash", None)
    with SessionLocal() as db:
        items = [{"id": t.id, "name": t.name,
                  "fee_text": doc_templates.format_pesos(t.default_fee_centavos) if t.default_fee_centavos else "No fee"}
                 for t in db.query(DocumentTemplate).filter(DocumentTemplate.is_active.is_(True))
                              .order_by(DocumentTemplate.id).all()]
    return templates.TemplateResponse(request, "documents_issue.html", {
        "items": items, "message": message, "client_name": carry.get("client_name", ""),
        "suffix": ("?" + urlencode(carry)) if carry else "",
        "names_missing": signatories_missing(branding.get_branding()),
    })

# ---- the issue form
def render_issue_form(request: Request, row, form: dict, ticket_number: str, ticket_date: str, copy_of: str,
                      error: str | None = None, status_code: int = 200):
    token = secrets.token_urlsafe(16)               # one use only: a second press of Print, or Back, cannot issue twice
    request.session["issue_token"] = token
    used = doc_templates.fields_used(effective_body(row))
    b = branding.get_branding()
    return templates.TemplateResponse(request, "document_form.html", {
        "t": {"id": row.id, "name": row.name, "extras": [e for e in row.extras.split(",") if e]},
        "look": look_of(row),
        "design": documents.for_print(design_of(row)),
        "shown": form, "error": error, "token": token, "today": date.today().isoformat(),
        "civil_statuses": documents.CIVIL_STATUSES,
        "required": {"address": "Address" in used, "purpose": "Purpose" in used},
        "ticket_number": ticket_number, "ticket_date": ticket_date, "copy_of": copy_of,
        "signer_options": signer_options(b),
        # choice -> titles whose names are missing; the page disables Print for a choice that has any
        "missing": {choice: documents.missing_signers(b, choice) for choice in doc_templates.SIGNER_CHOICES},
    }, status_code=status_code)

def voided_original(db, copy_of: str, template_id: int):
    """The voided document a corrected copy is made from (None if it is not a voided one of this template)."""
    original = db.get(IssuedDocument, parse_optional_int(copy_of)) if parse_optional_int(copy_of) else None
    if original and original.status == "voided" and original.template_id == template_id:
        return original
    return None

@app.get("/documents/issue/{template_id}", dependencies=[Depends(require("issue"))])
def issue_form(request: Request, template_id: int, client_name: str = "", address: str = "", ticket: str = "",
               ticket_number: str = "", ticket_date: str = "", copy_of: str = ""):
    with SessionLocal() as db:
        row = db.get(DocumentTemplate, template_id)
        if row is None:
            return RedirectResponse(url="/documents", status_code=303)
        original = voided_original(db, copy_of, template_id)
        if not row.is_active and original is None:
            flash(request, False, f'"{row.name}" is turned off. Choose another document.')
            return RedirectResponse(url="/documents", status_code=303)

        carry = carry_params(client_name, address, ticket_number, ticket_date)
        ticket_id = parse_optional_int(ticket)
        queued = db.get(Ticket, ticket_id) if ticket_id else None
        if queued:                                   # opened from the queue: copy the visitor's details
            carry.setdefault("client_name", queued.citizen_name)
            if queued.address:
                carry.setdefault("address", queued.address)
            carry["ticket_number"], carry["ticket_date"] = queued.ticket_number, queued.created_at.date().isoformat()

        form = {"client_name": carry.get("client_name", ""), "address": carry.get("address", ""), "purpose": "",
                "issue_date": date.today().isoformat(), "age": "", "civil_status": "",
                "nationality": documents.DEFAULT_NATIONALITY, "years": "",
                # The CTC "Issued on" starts on today's date (step 8e); she clears it if the client has no CTC
                "ctc_number": "", "ctc_place": "", "ctc_date": date.today().isoformat(),
                "fee": doc_templates.format_fee_input(row.default_fee_centavos), "or_choice": "", "or_number": "",
                "signers": row.signers}
        if original:                                 # a corrected copy starts from the voided one (dated today)
            old = documents.from_json(original.extras, {})
            form.update({"client_name": original.recipient, "address": original.address or "",
                         "purpose": original.purpose or "", "age": old.get("age", ""),
                         "civil_status": old.get("civil_status", ""), "years": old.get("years", ""),
                         "nationality": old.get("nationality", documents.DEFAULT_NATIONALITY),
                         "ctc_number": old.get("ctc_number", ""), "ctc_place": old.get("ctc_place", ""),
                         "ctc_date": old.get("ctc_date", ""),
                         "signers": documents.from_json(original.print_snapshot, {}).get("signers", row.signers),
                         "fee": doc_templates.format_fee_input(original.fee_centavos),
                         "or_choice": {"provided": "enter", "not_available": "none"}.get(original.or_status, ""),
                         "or_number": original.or_number or ""})
            carry["ticket_number"], carry["ticket_date"] = original.ticket_number or "", (
                original.ticket_date.isoformat() if original.ticket_date else "")
        return render_issue_form(request, row, form, carry.get("ticket_number", ""), carry.get("ticket_date", ""),
                                 str(original.id) if original else "")

@app.post("/documents/issue/{template_id}/preview", dependencies=[Depends(require("issue"))])
def preview_issue(template_id: int, client_name: str = Form(""), address: str = Form(""), purpose: str = Form(""),
                  issue_date: str = Form(""), age: str = Form(""), civil_status: str = Form(""),
                  nationality: str = Form(""), years: str = Form(""), fee: str = Form(""), or_choice: str = Form(""),
                  or_number: str = Form(""), ctc_number: str = Form(""), ctc_place: str = Form(""),
                  ctc_date: str = Form("")):
    """The live preview next to the form: the same code that prints, with a [Field name] where
    something is not typed yet. Saves nothing and uses no control number."""
    with SessionLocal() as db:
        row = db.get(DocumentTemplate, template_id)
        if row is None:
            return JSONResponse({"paragraphs": [], "fee_line": None})
        body, months, print_fee = effective_body(row), row.validity_months, bool(row.print_fee)
        auto_bold = bool(row.auto_bold)
    try:
        when = documents.parse_issue_date(issue_date, date.today())
    except documents.IssueError:
        when = date.today()
    try:
        fee_centavos = doc_templates.parse_fee(fee)
    except ValueError:
        fee_centavos = 0
    number = clean_text(or_number, 40) if or_choice == documents.OR_ENTER and fee_centavos > 0 else ""
    b = branding.get_branding()
    entry = {"client_name": clean_text(client_name, 100) or "[Client name]",
             "address": clean_text(address, 150) or "[Address]", "purpose": clean_text(purpose, 150) or "[Purpose]",
             "issue_date": when, "fee_centavos": fee_centavos, "or_number": number or None,
             "extras": {"age": clean_text(age, 3) or "[Age]", "civil_status": clean_text(civil_status, 20) or "[Civil status]",
                        "nationality": clean_text(nationality, 40) or "[Nationality]",
                        "ctc_number": clean_text(ctc_number, 30), "ctc_place": clean_text(ctc_place, 60),
                        "ctc_date": ctc_date.strip(),
                        "years": clean_text(years, 3) or "[Years of residence]"}}
    paragraphs = doc_templates.render_paragraphs(
        body, documents.values_for(entry, b, months, control_number="[Control number]"), auto_bold)
    return JSONResponse({"paragraphs": paragraphs,
                         "fee_line": documents.fee_line(fee_centavos, "provided" if number else "not_available", number, print_fee)})

@app.post("/documents/issue/{template_id}", dependencies=[Depends(require("issue"))])
def issue_document(request: Request, template_id: int, issue_token: str = Form(""),
                   client_name: str = Form(""), address: str = Form(""), purpose: str = Form(""),
                   issue_date: str = Form(""), age: str = Form(""), civil_status: str = Form(""),
                   nationality: str = Form(""), years: str = Form(""), fee: str = Form(""), or_choice: str = Form(""),
                   or_number: str = Form(""), signers: str = Form(""),
                   ctc_number: str = Form(""), ctc_place: str = Form(""), ctc_date: str = Form(""),
                   ticket_number: str = Form(""), ticket_date: str = Form(""), copy_of: str = Form("")):
    expected = request.session.pop("issue_token", None)
    if not expected or not secrets.compare_digest(expected, issue_token):
        flash(request, False, "That form was already used or has expired, so nothing was saved. Please start again.")
        return RedirectResponse(url="/documents", status_code=303)

    ticket_number = ticket_number if TICKET_RE.match(ticket_number) else ""
    ticket_day = parse_date(ticket_date) if ticket_number else None
    raw = {"client_name": clean_text(client_name, 100), "address": clean_text(address, 150),
           "purpose": clean_text(purpose, 150), "issue_date": issue_date.strip(), "age": age.strip(),
           "civil_status": civil_status.strip(), "nationality": clean_text(nationality, 40), "years": years.strip(),
           "fee": fee.strip(), "or_choice": or_choice.strip(), "or_number": clean_text(or_number, 40),
           "signers": signers.strip(), "ctc_number": clean_text(ctc_number, 30),
           "ctc_place": clean_text(ctc_place, 60), "ctc_date": ctc_date.strip()}
    b = branding.get_branding()
    with SessionLocal() as db:
        row = db.get(DocumentTemplate, template_id)
        if row is None:
            return RedirectResponse(url="/documents", status_code=303)
        original = voided_original(db, copy_of, template_id)
        if not row.is_active and original is None:
            flash(request, False, f'"{row.name}" is turned off. Choose another document.')
            return RedirectResponse(url="/documents", status_code=303)
        corrected_of = original.control_number if original else None     # read now: the session closes below

        def fail(message: str):
            return render_issue_form(request, row, raw, ticket_number, ticket_date if ticket_day else "",
                                     str(original.id) if original else "", message, 400)

        body = effective_body(row)
        extras = [e for e in row.extras.split(",") if e]
        problem = doc_templates.validate_body(body, doc_templates.available_fields(extras, row.validity_months is not None))
        if problem:
            return fail(f"The wording of this document has a problem, so it cannot be issued: {problem} Fix it under Templates.")
        try:
            entry = documents.read_issue_form(raw, body=body, extras=extras, today=date.today())
        except documents.IssueError as e:
            return fail(str(e))
        missing = documents.missing_signers(b, entry["signers"])
        if missing:
            return fail(f"The name of the {' and the '.join(missing)} is not set. Fill it in under Settings, "
                        "or choose another signer, then try again.")

        admin = get_current_admin(request)
        try:
            # The number first, then the wording: {Control number} is filled in with it. Both are in
            # the same save, so a failed save gives the number back (no gap).
            number = issue_control_number(db, row.code_prefix, date.today().year, b["control_number_format"])
            paragraphs = doc_templates.render_paragraphs(
                body, documents.values_for(entry, b, row.validity_months, control_number=number), bool(row.auto_bold))
            record = IssuedDocument(
                control_number=number, template_id=row.id, template_name=row.name, recipient=entry["client_name"],
                address=entry["address"] or None, purpose=entry["purpose"] or None, issue_date=entry["issue_date"],
                extras=documents.to_json(entry["extras"]), fee_centavos=entry["fee_centavos"],
                or_number=entry["or_number"], or_status=entry["or_status"],
                final_text=documents.to_json(paragraphs),                      # frozen: a later template edit never changes it
                print_snapshot=documents.to_json(documents.make_snapshot(
                    b, row.print_fee, signers=entry["signers"], design=design_of(row), text_look=look_of(row))),
                issued_by=admin.username if admin else "unknown",
                ticket_number=ticket_number or None, ticket_date=ticket_day, status="issued",
                created_at=datetime.now())
            db.add(record)
            db.commit()
            record_id, record_number, template_name = record.id, number, row.name
        except IntegrityError:
            db.rollback()
            log.exception("Saving an issued document failed")
            return fail("The document could not be saved. Nothing was printed. Please try again.")

    note = f"; fee {doc_templates.format_pesos(entry['fee_centavos'])} ({entry['or_status'].replace('_', ' ')})" if entry["fee_centavos"] else ""
    if corrected_of:
        note += f"; corrected copy of {corrected_of}"
    log_action("Documents", "Document Issued", f'Issued {record_number} ({template_name}) for {entry["client_name"]}{note}', request)
    return RedirectResponse(url=f"/documents/issued/{record_id}/print?first=1", status_code=303)

# ---- the print page (first print, and reprints)
@app.get("/documents/issued/{doc_id}/print", dependencies=[Depends(require("issue", "view_issued"))])
def print_document(request: Request, doc_id: int, first: str = ""):
    b = branding.get_branding()
    with SessionLocal() as db:
        rec = db.get(IssuedDocument, doc_id)
        if rec is None:
            return RedirectResponse(url="/documents/history", status_code=303)
        snapshot = documents.from_json(rec.print_snapshot, {})
        view = {"control_number": rec.control_number, "template_name": rec.template_name,
                "void_reason": rec.void_reason, "issue_date_text": rec.issue_date.strftime("%b %d, %Y")}
        voided, has_ticket = rec.status == "voided", rec.ticket_number is not None
        paragraphs = documents.from_json(rec.final_text, [])
        fee_text = documents.fee_line(rec.fee_centavos, rec.or_status, rec.or_number, snapshot.get("print_fee", True))
    is_first = first == "1" and not voided
    if not is_first and not voided:
        log_action("Documents", "Document Reprinted", f"Opened {view['control_number']} for printing", request)
    return templates.TemplateResponse(request, "document_print.html", {
        "rec": view, "voided": voided, "first": is_first, "has_ticket": has_ticket,
        "paragraphs": [doc_templates.paragraph_lines(p) for p in paragraphs], "fee_line": fee_text,
        "tab": doc_templates.TAB,
        # Documents issued before step 8b: a plain page at the old size
        "design": documents.for_print(documents.snapshot_design(snapshot)),
        # Documents issued before step 8a: indented, both signers, and the number on its own fixed line
        "number_line": not snapshot.get("number_in_text", False),
        "look": documents.snapshot_look(snapshot),              # the spacing it was printed with
        "sign_lines": [{"name": snapshot.get(f"{who}_name", b[f"{who}_name"]),
                        "title": snapshot.get(f"{who}_title", b[f"{who}_title"])}
                       for who in documents.SIGNER_LINES.get(snapshot.get("signers", "both"), documents.SIGNER_LINES["both"])],
        # Names and header lines come from the saved copy (as issued); the logos are today's
        # Documents issued before step 8c kept the name as one line ("Barangay X, City, Province")
        "hdr": {"name": snapshot.get("name", snapshot.get("full_name", b["full_name"])),
                "place": snapshot.get("place", "" if "full_name" in snapshot else b["place"]),
                "office_color": branding.clean_color(snapshot.get("office_color", "")) or b["office_color"],
                "header_line_1": snapshot.get("header_line_1", b["header_line_1"]),
                "header_line_2": snapshot.get("header_line_2", b["header_line_2"]), "logos": b["logos"]},
    })
    
# ---- the history (the "Issued documents" tab)
def history_query(db, d_from, d_to, template_id, status, receipt, q):
    query = db.query(IssuedDocument).filter(IssuedDocument.issue_date >= d_from, IssuedDocument.issue_date <= d_to)
    if template_id:
        query = query.filter(IssuedDocument.template_id == template_id)
    if status:
        query = query.filter(IssuedDocument.status == status)
    if receipt == "no_or":
        query = query.filter(IssuedDocument.or_status == "not_available")
    if q:
        like = like_pattern(q)
        query = query.filter(IssuedDocument.control_number.ilike(like, escape="\\")
                             | IssuedDocument.recipient.ilike(like, escape="\\"))
    return query

def history_filters(date_from, date_to, template_id, status, receipt):
    d_from, d_to = documents.history_range(date_from, date_to, date.today())
    return (d_from, d_to, parse_optional_int(template_id), status if status in ("issued", "voided") else "",
            "no_or" if receipt == "no_or" else "")

@app.get("/documents/history", dependencies=[Depends(require("view_issued"))])
def documents_history(request: Request, date_from: str = "", date_to: str = "", template_id: str = "",
                      status: str = "", receipt: str = "", q: str = "", page: str = "1"):
    d_from, d_to, tid, status, receipt = history_filters(date_from, date_to, template_id, status, receipt)
    message = request.session.pop("flash", None)
    with SessionLocal() as db:
        base = history_query(db, d_from, d_to, tid, status, receipt, q)

        # Totals leave voided documents out. They follow the filters, like the list.
        live = base.filter(IssuedDocument.status == "issued")
        fees = func.coalesce(func.sum(IssuedDocument.fee_centavos), 0)
        issued_count, fees_total = live.with_entities(func.count(IssuedDocument.id), fees).one()
        no_or_count, no_or_total = live.filter(IssuedDocument.or_status == "not_available") \
            .with_entities(func.count(IssuedDocument.id), fees).one()
        money = doc_templates.format_pesos
        by_day = live.with_entities(IssuedDocument.issue_date, IssuedDocument.template_name,
                                    func.count(IssuedDocument.id), fees) \
            .group_by(IssuedDocument.issue_date, IssuedDocument.template_name) \
            .order_by(IssuedDocument.issue_date.desc(), IssuedDocument.template_name).all()
        by_template = live.with_entities(IssuedDocument.template_name, func.count(IssuedDocument.id), fees) \
            .group_by(IssuedDocument.template_name).order_by(IssuedDocument.template_name).all()
        totals = {
            "issued_count": issued_count, "fees_text": money(fees_total),
            "voided_count": base.filter(IssuedDocument.status == "voided").count(),
            "no_or_count": no_or_count, "no_or_text": money(no_or_total),
            "by_day": [{"date_text": d.strftime("%b %d, %Y"), "template_name": n, "count": c, "fees_text": money(f)}
                       for d, n, c, f in by_day],
            "by_template": [{"template_name": n, "count": c, "fees_text": money(f)} for n, c, f in by_template],
        }

        total_records = base.count()
        page_number = parse_optional_int(page) or 1
        total_pages = max(1, math.ceil(total_records / PAGE_SIZE))
        page_number = max(1, min(page_number, total_pages))
        records = (base.order_by(IssuedDocument.created_at.desc(), IssuedDocument.id.desc())
                   .offset((page_number - 1) * PAGE_SIZE).limit(PAGE_SIZE).all())
        rows = [{"id": r.id, "control_number": r.control_number, "template_id": r.template_id,
                 "template_name": r.template_name, "recipient": r.recipient, "address": r.address,
                 "issue_date_text": r.issue_date.strftime("%m/%d/%Y"),
                 "fee_text": money(r.fee_centavos) if r.fee_centavos else "No fee",
                 "or_status": r.or_status, "or_number": r.or_number, "voided": r.status == "voided"} for r in records]
        template_choices = [{"id": t.id, "name": t.name} for t in db.query(DocumentTemplate).order_by(DocumentTemplate.id).all()]

    return templates.TemplateResponse(request, "documents_history.html", {
        "rows": rows, "totals": totals, "message": message, "template_choices": template_choices,
        "date_from": d_from.isoformat(), "date_to": d_to.isoformat(), "template_id": tid,
        "status": status, "receipt": receipt, "search": q,
        "page": page_number, "total_pages": total_pages, "total_records": total_records,
        "start_record": (page_number - 1) * PAGE_SIZE + 1 if total_records else 0,
        "end_record": min(page_number * PAGE_SIZE, total_records),
        "back": str(request.url.path) + (("?" + request.url.query) if request.url.query else ""),
    })

@app.get("/documents/history/export", dependencies=[Depends(require("view_issued"))])
def documents_export(request: Request, date_from: str = "", date_to: str = "", template_id: str = "",
                     status: str = "", receipt: str = "", q: str = ""):
    """The list as a CSV file: same filters as the page, with the fee, OR number and OR status."""
    d_from, d_to, tid, status, receipt = history_filters(date_from, date_to, template_id, status, receipt)
    with SessionLocal() as db:
        records = history_query(db, d_from, d_to, tid, status, receipt, q) \
            .order_by(IssuedDocument.issue_date, IssuedDocument.id).all()
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Control number", "Date", "Document", "Client name", "Address", "Purpose", "Fee (PHP)",
                         "Receipt status", "OR number", "Issued by", "Status", "Void reason"])
        for r in records:
            writer.writerow([csv_safe(r.control_number), r.issue_date.strftime("%m/%d/%Y"), csv_safe(r.template_name),
                             csv_safe(r.recipient), csv_safe(r.address), csv_safe(r.purpose),
                             documents.pesos_plain(r.fee_centavos), r.or_status.replace("_", " ").title(),
                             csv_safe(r.or_number), csv_safe(r.issued_by), r.status.title(), csv_safe(r.void_reason)])
    log_action("Documents", "CSV Export",
               f"Exported {len(records)} issued document(s) for {range_label(d_from, d_to)}", request)
    return Response(
        content="\ufeff" + buffer.getvalue(),            # BOM so Excel reads UTF-8 names correctly
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="issued_documents_{d_from}_to_{d_to}.csv"'})
    
# ---- void, and adding a missing OR number
@app.post("/documents/issued/{doc_id}/void", dependencies=[Depends(require("void"))])
def void_document(request: Request, doc_id: int, reason: str = Form(""), back: str = Form("")):
    reason = clean_text(reason, 200)
    if not reason:
        flash(request, False, "Please type why the document is being voided. Nothing was changed.")
        return RedirectResponse(url=safe_back(back), status_code=303)
    with SessionLocal() as db:
        # One atomic change: if two clicks arrive together, only one of them voids it
        changed = db.query(IssuedDocument).filter(IssuedDocument.id == doc_id, IssuedDocument.status == "issued") \
            .update({"status": "voided", "void_reason": reason}, synchronize_session=False)
        db.commit()
        number = db.query(IssuedDocument.control_number).filter(IssuedDocument.id == doc_id).scalar() if changed == 1 else None
    if number:
        log_action("Documents", "Document Voided", f"Voided {number}: {reason}", request)
        flash(request, True, f"{number} was voided. Use Issue a corrected copy to make a new one.")
    else:
        flash(request, False, "That document could not be voided. It may already be voided.")
    return RedirectResponse(url=safe_back(back), status_code=303)

@app.post("/documents/issued/{doc_id}/or", dependencies=[Depends(require("add_or"))])
def add_or_number(request: Request, doc_id: int, or_number: str = Form(""), back: str = Form("")):
    """A receipt number that was missing at issue time. The certificate text is not touched."""
    number_typed = clean_text(or_number, 40)
    if not number_typed:
        flash(request, False, "Please type the OR number. Nothing was changed.")
        return RedirectResponse(url=safe_back(back), status_code=303)
    with SessionLocal() as db:
        changed = db.query(IssuedDocument).filter(
            IssuedDocument.id == doc_id, IssuedDocument.status == "issued", IssuedDocument.or_status == "not_available") \
            .update({"or_number": number_typed, "or_status": "provided"}, synchronize_session=False)
        db.commit()
        number = db.query(IssuedDocument.control_number).filter(IssuedDocument.id == doc_id).scalar() if changed == 1 else None
    if number:
        log_action("Documents", "OR Number Added", f"Added OR number {number_typed} to {number}", request)
        flash(request, True, f"The OR number was added to {number}.")
    else:
        flash(request, False, "The OR number could not be added. The document may be voided or already have one.")
    return RedirectResponse(url=safe_back(back), status_code=303)


# ABOUT
TERMS_FILE = Path(resource_path("terms_and_conditions.txt"))         # the same terms the installer shows
LICENSES_FILE = Path(resource_path("third_party_licenses.json"))     # made by make_notices.py (build.bat)

def newest_backup(folder: Path):
    """When the newest backup in a folder was made (automatic, or from the Maintenance page), or None."""
    try:
        times = [f.stat().st_mtime for f in folder.iterdir()
                 if f.is_file() and f.suffix == ".db" and f.name.startswith(("auto_", "barangay_system_"))]
    except OSError:
        return None
    return datetime.fromtimestamp(max(times)) if times else None

def system_details() -> list[tuple[str, str]]:
    """What the developer needs to know when the office asks for help, as (label, value) rows.
    Read fresh each time the About page opens; nothing here is secret."""
    rows = [("App", f"{APP_TITLE} {APP_VERSION}"),
            ("Installed for", branding.get_branding()["full_name"])]

    db_file = Path(data_path("barangay_system.db"))
    try:
        size = f"{db_file.stat().st_size / 1048576:.2f} MB"
    except OSError:
        size = "size unknown"
    try:
        with engine.connect() as con:
            version = con.exec_driver_sql("PRAGMA user_version").scalar()
        rows.append(("Database", f"version {version} of {DB_LATEST}, {size}"))
    except Exception:
        log.exception("About page: could not read the database version")
        rows.append(("Database", f"version unknown, {size}"))

    last = newest_backup(BACKUP_DIR_DEFAULT)
    rows.append(("Last backup", f"{last:%Y-%m-%d %I:%M %p}" if last else "None yet"))
    rows.append(("Backup folder", str(BACKUP_DIR_DEFAULT)))
    extra = get_extra_backup_dir()
    if extra is None:
        rows.append(("Second backup folder", "Not set"))
    else:
        rows.append(("Second backup folder", str(extra) if extra.is_dir() else f"Not found: {extra}"))

    p = load_printer_settings()
    printer = f"{p['printer_name']} ({p['paper_mm']} mm paper)" if p["printer_name"] else "Not chosen yet"
    if USE_DUMMY_PRINTER:
        printer += ", test mode (slips are saved to a file, not printed)"
    rows.append(("Receipt printer", printer))

    rows.append(("Data folder", str(DATA_DIR)))
    rows.append(("Windows", f"{platform.system()} {platform.release()} ({platform.version()})"))
    rows.append(("Computer name", platform.node() or "unknown"))
    rows.append(("Checked on", f"{datetime.now():%Y-%m-%d %I:%M %p}"))
    return rows

def terms_text() -> str:
    try:
        return TERMS_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""

def bundled_licenses() -> list[dict]:
    """Name, version and license of each open-source part inside the app (empty until build.bat has run)."""
    try:
        rows = json.loads(LICENSES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict) and all(isinstance(r.get(k), str) for k in ("name", "version", "license"))]

@app.get("/about", dependencies=[Depends(login_required)])
def show_about(request: Request):
    return templates.TemplateResponse(request, "about.html", {
        "released": APP_RELEASED,
        "details": system_details(),
        "terms": terms_text(),
        "licenses": bundled_licenses(),
    })


@app.post("/preferences/theme", dependencies=[Depends(login_required)])
def set_theme(theme: str = Form("")):
    """The staff app's light / dark / system choice, from the sun-moon menu in the top bar."""
    if theme not in THEMES:
        return JSONResponse({"error": "Unknown theme."}, status_code=400)
    try:
        save_ui_pref("theme", theme)
    except OSError:
        log.exception("Could not save the theme")
        return JSONResponse({"error": "The setting could not be saved."}, status_code=500)
    return {"theme": theme}


@app.post("/display/theme")
def set_display_theme(theme: str = Form("")):
    """The display screen's light / dark choice. No sign-in, like the display page itself; the
    cross-origin check above still applies, and the only possible values are light and dark."""
    if theme not in DISPLAY_THEMES:
        return JSONResponse({"error": "Unknown theme."}, status_code=400)
    try:
        save_ui_pref("display_theme", theme)
    except OSError:
        log.exception("Could not save the display theme")
        return JSONResponse({"error": "The setting could not be saved."}, status_code=500)
    return {"theme": theme}


@app.get("/display")
def display_page(request: Request):
    return templates.TemplateResponse(request, "display.html", {})

@app.get("/display/data")
def display_data():
    start = datetime.combine(date.today(), time.min)
    with SessionLocal() as db:
        current = db.query(Ticket).filter(Ticket.status == "called", Ticket.created_at >= start) \
                    .order_by(Ticket.called_at.desc()).first()
        now_serving = current.ticket_number if current else None
    return {"now_serving": now_serving, "upcoming": [t.ticket_number for t in get_incoming_order(limit=3)]}

open_display_window = None

def signed_in_home(request: Request) -> str:
    """The start page of whoever is signed in, for routes that have no guard of their own."""
    try:
        check_signed_in(request)
    except NotAuthenticated:
        return "/login"
    return home_url(request)


@app.post("/display/open")
def open_display(request: Request):
    if open_display_window is None:
        flash(request, True, f"Open {request.base_url}display in another browser window.")
        return RedirectResponse(url=signed_in_home(request), status_code=303)
    try:
        open_display_window()
    except Exception:
        log.exception("Could not open the display window")
        flash(request, False, "The display screen could not be opened. See app.log.")
        return RedirectResponse(url=signed_in_home(request), status_code=303)
    return Response(status_code=204)