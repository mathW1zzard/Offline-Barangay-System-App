"""Makes the list of open-source parts inside the app, from what is installed in this environment.

build.bat runs it before PyInstaller, so the list always matches the versions that get bundled:
  THIRD_PARTY_NOTICES.txt     every part with its full license text (installed next to the app)
  third_party_licenses.json   name, version and license of each part (read by the About page)

Run it by hand with:  python make_notices.py
For the Attendance edition (build_attendance.bat does this): set BQS_EDITION=attendance first. Its
notices go to THIRD_PARTY_NOTICES_Attendance.txt, so building one app never changes the other's file.
"""
import json
import re
import sys
from importlib import metadata
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import edition                          # noqa: E402  (which app is being built: see edition.py)

APP_TITLE = edition.APP_TITLE
NOTICES_FILE = edition.NOTICES_FILE     # THIRD_PARTY_NOTICES.txt, or THIRD_PARTY_NOTICES_Attendance.txt

# Tools used only to build or develop the app. They are never inside the installed app.
NOT_SHIPPED = {"pip", "wheel", "pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile",
               "pywin32-ctypes", "git-filter-repo", "alembic", "mako"}

LICENSE_FILE = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", re.IGNORECASE)
MAX_TEXT = 200_000          # characters of license text kept per part (Pillow's is the longest, about 60k)


def normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def license_name(dist) -> str:
    """The short name of a package's license, from its own metadata."""
    md = dist.metadata
    expression = (md.get("License-Expression") or "").strip()
    if expression:
        return expression
    from_classifiers = [c.split(" :: ")[-1] for c in (md.get_all("Classifier") or [])
                        if c.startswith("License :: ") and c != "License :: OSI Approved"]
    if from_classifiers:
        return ", ".join(dict.fromkeys(from_classifiers))
    field = (md.get("License") or "").strip()
    if field and "\n" not in field and len(field) <= 60:
        return field
    return "See the license text"


def license_texts(dist) -> list[str]:
    """The license files a package ships in its .dist-info folder (or the long License field)."""
    texts = []
    for f in dist.files or []:
        if LICENSE_FILE.match(f.name) and any(part.endswith(".dist-info") for part in f.parts):
            try:
                texts.append(Path(f.locate()).read_text(encoding="utf-8", errors="replace").strip())
            except OSError:
                pass
    if not texts:
        field = (dist.metadata.get("License") or "").strip()
        if "\n" in field or len(field) > 60:
            texts.append(field)
    return [t[:MAX_TEXT] for t in texts if t]


def packages() -> list[dict]:
    seen, rows = set(), []
    for dist in metadata.distributions():
        name = dist.metadata.get("Name") or ""
        key = normal(name)
        if not name or key in seen or key in NOT_SHIPPED:
            continue
        seen.add(key)
        rows.append({"name": name, "version": dist.version or "",
                     "license": license_name(dist), "texts": license_texts(dist)})
    return sorted(rows, key=lambda r: r["name"].lower())


def python_itself() -> dict:
    """The Python runtime that PyInstaller puts inside the app."""
    text = ""
    for place in (Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE"):
        if place.is_file():
            text = place.read_text(encoding="utf-8", errors="replace").strip()
            break
    return {"name": "Python", "version": sys.version.split()[0], "license": "PSF-2.0",
            "texts": [text] if text else []}


def the_font() -> dict:
    ofl = HERE / "static" / "fonts" / "OFL.txt"
    text = ofl.read_text(encoding="utf-8").strip() if ofl.is_file() else ""
    if not text:
        raise SystemExit("static/fonts/OFL.txt is missing: the font's license must ship with the font.")
    return {"name": "Plus Jakarta Sans (font)", "version": "", "license": "OFL-1.1", "texts": [text]}


def main():
    rows = [python_itself()] + packages() + [the_font()]

    missing = [r["name"] for r in rows if not r["texts"]]
    lines = [f"THIRD-PARTY NOTICES - {APP_TITLE}", "",
             f"{APP_TITLE} includes the open-source software listed below. Each part keeps its own",
             "license, which is copied here in full. These licenses apply to those parts only.", ""]
    for r in rows:
        title = f"{r['name']} {r['version']}".strip()
        lines += ["=" * 78, f"{title}  -  {r['license']}", "=" * 78, ""]
        lines += ["\n\n".join(r["texts"]) if r["texts"] else "(No license file was found in this package.)", ""]
    (HERE / NOTICES_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\r\n")

    short = [{"name": r["name"], "version": r["version"], "license": r["license"]} for r in rows]
    (HERE / "third_party_licenses.json").write_text(json.dumps(short, indent=1), encoding="utf-8")

    print(f"Listed {len(rows)} open-source parts in {NOTICES_FILE} and third_party_licenses.json")
    if missing:
        print("No license file found for: " + ", ".join(missing) + " (check these by hand)")


if __name__ == "__main__":
    main()
