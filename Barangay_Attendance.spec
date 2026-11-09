# -*- mode: python ; coding: utf-8 -*-
# The ATTENDANCE EDITION: the same code as Barangay_System.spec, without Documents and Staff accounts.
# Build with build_attendance.bat (it makes the license list first and keeps this build in its own folders).
# Result:      dist_attendance\Barangay_Attendance\Barangay_Attendance.exe   (the whole folder is the app)

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# uvicorn, the web-socket library and the form parser are loaded by name at run time,
# so PyInstaller cannot see them by reading the code and they must be listed here.
hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("websockets")
    + ["multipart", "python_multipart", "win32print", "pywintypes"]
)

datas = [
    ("templates", "templates"),
    ("static", "static"),          # includes static/fonts and static/audio
    ("app_icon.ico", "."),
    ("terms_and_conditions_attendance.txt", "."),   # shown on the About page
    ("third_party_licenses.json", "."),    # made by make_notices.py; build_attendance.bat runs it first
    ("edition_attendance.txt", "."),       # renamed to edition.txt below: this file is what makes it the Attendance edition
]

datas += collect_data_files("escpos")

# edition.py reads edition.txt from the bundle. Bundle it under that exact name, and fail the build if the
# source file is missing (a build without it would quietly be the full Barangay System).
import shutil
from pathlib import Path
Path("build_attendance").mkdir(exist_ok=True)
shutil.copyfile("edition_attendance.txt", "build_attendance/edition.txt")
datas = [d for d in datas if d[0] != "edition_attendance.txt"] + [("build_attendance/edition.txt", ".")]

a = Analysis(
    ["run_app.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["alembic", "tkinter"],   # alembic is only for developing the database
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Barangay_Attendance",
    icon="app_icon.ico",
    console=False,      # no black console window
    upx=False,          # UPX makes antivirus false alarms more likely
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name="Barangay_Attendance",
)
