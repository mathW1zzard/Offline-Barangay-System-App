"""Reading a certificate design out of a Word file (.docx), without Word.

A .docx file is a zip folder of XML files and pictures. The design of a certificate usually lives in
the page HEADER and FOOTER: banners, logos and seals placed as pictures, often "behind the text".
This module reads, from the first section of the file:
  - the paper size and the margins,
  - every picture in the first-page header and footer (or the normal ones), with the position and
    size Word gives it, its crop, and whether it is behind or in front of the text,
and says plainly what it could NOT read (text boxes, shapes, WordArt, typed header text, pictures in
old formats), so the secretary knows what will be missing.

Positions are worked out the way Word does it for the common cases. A picture placed relative to a
paragraph in the footer needs the height of the footer text, which is estimated from its font and
spacing, so such a picture can be off by a millimetre or two.

Safety: the file comes from outside, so it is opened with limits (size, number of parts), only the
parts that are needed are read, and XML with a DOCTYPE (used by "XML bomb" attacks) is refused.
No web code and no database code here: pictures are handed to a `store` function (main.py passes
pictures.store), so this file can be tested on its own.
"""
import io
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

MAX_FILE_BYTES = 15 * 1024 * 1024        # the .docx itself
MAX_PARTS = 2000                          # files inside the zip
MAX_XML_BYTES = 5 * 1024 * 1024           # one XML part, unzipped
MAX_MEDIA_BYTES = 20 * 1024 * 1024        # one picture, unzipped

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "v": "urn:schemas-microsoft-com:vml",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
W = "{%s}" % NS["w"]
R = "{%s}" % NS["r"]

TWIP_MM = 25.4 / 1440                     # Word measures the page in twips (1/1440 inch)
EMU_MM = 1 / 36000                        # and pictures in EMU (914400 per inch)
# How tall one line of text is, as a share of the font size (from the fonts' own metrics)
LINE_FACTOR = {"calibri": 1.22, "open sans": 1.362, "arial": 1.15, "arial narrow": 1.15, "times new roman": 1.15,
               "cambria": 1.17, "aptos": 1.22, "segoe ui": 1.33, "century gothic": 1.23, "verdana": 1.22,
               "tahoma": 1.21, "georgia": 1.14, "garamond": 1.12, "bookman old style": 1.17}


class DocxError(Exception):
    """The message is written for the secretary and is safe to show as it is."""


def _xml(data: bytes):
    if b"<!DOCTYPE" in data[:4096].upper() or b"<!ENTITY" in data.upper():
        raise DocxError("This Word file has something unusual inside, so it was not opened. "
                        "Please save it again in Word or WPS (File, Save As, .docx) and try again.")
    return ET.fromstring(data)


class _Package:
    """The zip, opened safely: only named parts are read, each with a size limit."""

    def __init__(self, raw: bytes):
        if len(raw) > MAX_FILE_BYTES:
            raise DocxError(f"That Word file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB. "
                            "Please use a smaller file.")
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(raw))
            names = self.zip.namelist()
        except (zipfile.BadZipFile, OSError, ValueError):
            raise DocxError("That file could not be opened as a Word document. Please choose a .docx file "
                            "(an older .doc file must first be saved again as .docx).") from None
        if len(names) > MAX_PARTS or "word/document.xml" not in names:
            raise DocxError("That file is not a Word document (.docx).")
        self.names = set(names)

    def read(self, name: str, limit: int) -> bytes | None:
        if name not in self.names:
            return None
        info = self.zip.getinfo(name)
        if info.file_size > limit:
            raise DocxError("A part of this Word file is too large to read.")
        with self.zip.open(info) as part:
            data = part.read(limit + 1)             # never trust the size written in the zip
        if len(data) > limit:
            raise DocxError("A part of this Word file is too large to read.")
        return data

    def xml(self, name: str):
        data = self.read(name, MAX_XML_BYTES)
        return _xml(data) if data is not None else None

    def rels(self, part: str) -> dict:
        """Relationship id -> target part name, for one part (e.g. word/header2.xml)."""
        folder, file = posixpath.split(part)
        root = self.xml(posixpath.join(folder, "_rels", file + ".rels"))
        out = {}
        if root is None:
            return out
        for rel in root.findall("rel:Relationship", NS):
            if rel.get("TargetMode") == "External":
                continue                              # a linked (not embedded) picture: never fetched
            target = posixpath.normpath(posixpath.join(folder, rel.get("Target", "")))
            out[rel.get("Id")] = target
        return out


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- styles: how tall a paragraph is
class _Styles:
    def __init__(self, root):
        self.by_id, self.default = {}, None
        self.doc_ppr, self.doc_rpr = None, None
        if root is None:
            return
        for style in root.findall("w:style", NS):
            if style.get(W + "type") != "paragraph":
                continue
            self.by_id[style.get(W + "styleId")] = style
            if style.get(W + "default") == "1":
                self.default = style.get(W + "styleId")
        defaults = root.find("w:docDefaults", NS)
        if defaults is not None:
            self.doc_ppr = defaults.find("w:pPrDefault/w:pPr", NS)
            self.doc_rpr = defaults.find("w:rPrDefault/w:rPr", NS)

    def chain(self, style_id):
        """The style, then the style it is based on, and so on (most specific first)."""
        seen, out = set(), []
        while style_id and style_id in self.by_id and style_id not in seen:
            seen.add(style_id)
            style = self.by_id[style_id]
            out.append(style)
            based = style.find("w:basedOn", NS)
            style_id = based.get(W + "val") if based is not None else None
        return out

    def paragraph_height_mm(self, paragraph) -> float:
        """Spacing before + one line + spacing after, from the paragraph, its style chain and the
        document defaults (the first one that says something wins)."""
        ppr = paragraph.find("w:pPr", NS)
        style_ref = ppr.find("w:pStyle", NS) if ppr is not None else None
        styles = self.chain(style_ref.get(W + "val") if style_ref is not None else self.default)
        pprs = [ppr] + [s.find("w:pPr", NS) for s in styles] + [self.doc_ppr]
        rprs = ([ppr.find("w:rPr", NS)] if ppr is not None else []) + [s.find("w:rPr", NS) for s in styles] + [self.doc_rpr]

        def spacing(attr):
            for p in pprs:
                node = p.find("w:spacing", NS) if p is not None else None
                if node is not None and node.get(W + attr) is not None:
                    return node.get(W + attr)
            return None

        def run(tag, attr):
            for r in rprs:
                node = r.find(tag, NS) if r is not None else None
                if node is not None and node.get(W + attr) is not None:
                    return node.get(W + attr)
            return None

        size_pt = _int(run("w:sz", "val"), 22) / 2            # half-points; Word's own default is 11 pt
        font = (run("w:rFonts", "ascii") or "calibri").lower()
        natural = size_pt * LINE_FACTOR.get(font, 1.2)        # one line at single spacing, in pt
        line, rule = _int(spacing("line"), 240), spacing("lineRule") or "auto"
        if rule == "exact":
            line_pt = line / 20
        elif rule == "atLeast":
            line_pt = max(line / 20, natural)
        else:
            line_pt = natural * line / 240
        before, after = _int(spacing("before")) / 20, _int(spacing("after")) / 20
        return (before + line_pt + after) * 25.4 / 72


# ---------------------------------------------------------------- reading the file
def read_docx(raw: bytes, file_name: str = "") -> dict:
    """Returns {"page_w", "page_h", "margins": {top, bottom, left, right}, "items": [...], "notes": [...]}.
    Each item: {"data": picture bytes, "anchor": "top"/"bottom", "x", "y", "w", "h" (mm),
    "crop": [left, top, right, bottom] (shares 0..1), "front": bool}. "y" is measured from the top
    edge of the paper for a header picture and from the bottom edge for a footer picture (to the
    picture's near edge), so a footer picture stays at the bottom on shorter paper.
    Raises DocxError with a message for the secretary."""
    package = _Package(raw)
    document = package.xml("word/document.xml")
    doc_rels = package.rels("word/document.xml")
    styles = _Styles(package.xml("word/styles.xml"))

    section = document.find(".//w:sectPr", NS)                # the first section: where page 1 is
    if section is None:
        raise DocxError("The page setup of this Word file could not be found.")
    size = section.find("w:pgSz", NS)
    margins = section.find("w:pgMar", NS)
    page_w = _int(size.get(W + "w"), 12240) * TWIP_MM if size is not None else 215.9
    page_h = _int(size.get(W + "h"), 15840) * TWIP_MM if size is not None else 279.4
    if size is not None and size.get(W + "orient") == "landscape" and page_w < page_h:
        page_w, page_h = page_h, page_w
    m = {key: abs(_int(margins.get(W + key), default)) * TWIP_MM if margins is not None else default * TWIP_MM
         for key, default in (("top", 1440), ("bottom", 1440), ("left", 1440), ("right", 1440),
                              ("header", 720), ("footer", 720))}

    first_page = section.find("w:titlePg", NS) is not None
    def pick(kind):
        refs = {ref.get(W + "type", "default"): ref.get(R + "id") for ref in section.findall(f"w:{kind}Reference", NS)}
        rid = refs.get("first") if first_page and "first" in refs else refs.get("default")
        return doc_rels.get(rid) if rid else None

    items, notes = [], []
    for kind, anchor in (("header", "top"), ("footer", "bottom")):
        part = pick(kind)
        if not part:
            continue
        root = package.xml(part)
        if root is None:
            continue
        _read_part(package, part, root, anchor, page_w, page_h, m, styles, items, notes)

    if not items:
        notes.insert(0, "No pictures were found in the header or footer of this Word file.")
    return {"page_w": round(page_w, 2), "page_h": round(page_h, 2),
            "margins": {k: round(m[k], 2) for k in ("top", "bottom", "left", "right")},
            "items": items, "notes": _unique(notes), "file_name": file_name}


def _unique(notes):
    out = []
    for note in notes:
        if note not in out:
            out.append(note)
    return out


def _drop_hidden_copies(root):
    """Word saves some drawings twice: a modern copy (mc:Choice) and an old-format copy for old
    programs (mc:Fallback). Only the modern copy is read; the old copy is removed first."""
    fallback = "{%s}Fallback" % NS["mc"]
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag == fallback:
                parent.remove(child)


def _inside_text_box(paragraph):
    """Drawings inside a text box belong to the text box (which cannot be read); skip them."""
    boxed = set()
    for box in paragraph.iter("{%s}txbxContent" % NS["w"]):
        boxed.update(id(node) for node in box.iter())
    return boxed


def _read_part(package, part, root, anchor, page_w, page_h, m, styles, items, notes):
    _drop_hidden_copies(root)
    rels = package.rels(part)
    paragraphs = root.findall("w:p", NS) + root.findall(".//w:tc/w:p", NS)
    heights = [styles.paragraph_height_mm(p) for p in paragraphs]
    where = "header" if anchor == "top" else "footer"

    for index, paragraph in enumerate(paragraphs):
        if anchor == "top":                                      # header: text starts "header" mm from the top
            para_top = m["header"] + sum(heights[:index])
        else:                                                    # footer: text ends "footer" mm above the bottom
            para_top = page_h - m["footer"] - sum(heights[index:])

        typed = "".join(t.text or "" for t in paragraph.findall("w:r/w:t", NS)).strip()
        if typed:
            notes.append(f'Text typed in the {where} ("{typed[:40]}") cannot be read. Make it part of a picture, '
                         'or let the app print the logos and barangay name from Settings.')

        boxed = _inside_text_box(paragraph)
        for drawing in paragraph.iter(W + "drawing"):
            if id(drawing) in boxed:
                continue
            frame = drawing.find("wp:anchor", NS)
            inline = frame is None
            if inline:
                frame = drawing.find("wp:inline", NS)
            if frame is None:
                continue
            blip = frame.find(".//a:blip", NS)
            if blip is None:
                if frame.find(".//{http://schemas.microsoft.com/office/word/2010/wordprocessingShape}txbx") is not None:
                    notes.append(f"A text box in the {where} cannot be read and will not print.")
                else:
                    notes.append(f"A drawn shape or WordArt in the {where} cannot be read and will not print.")
                continue
            target = rels.get(blip.get(R + "embed"))
            data = package.read(target, MAX_MEDIA_BYTES) if target else None
            if not data:
                notes.append(f"A picture in the {where} is linked to a file outside the document and was left out.")
                continue
            if target.lower().endswith((".emf", ".wmf", ".svg", ".tif", ".tiff")):
                notes.append(f"A picture in the {where} is in a format the app cannot show "
                             f"({posixpath.splitext(target)[1][1:].upper()}). Insert it again as PNG or JPG.")
                continue
            extent = frame.find("wp:extent", NS)
            w = _int(extent.get("cx")) * EMU_MM if extent is not None else 0
            h = _int(extent.get("cy")) * EMU_MM if extent is not None else 0
            if w <= 0.5 or h <= 0.5:
                continue
            if inline:                                           # in the line of text: left edge, or centred
                jc = paragraph.find("w:pPr/w:jc", NS)
                align = jc.get(W + "val") if jc is not None else "left"
                x = _place(align, m["left"], page_w - m["left"] - m["right"], w, 0)
                y = para_top
                front = True
            else:
                x = _horizontal(frame.find("wp:positionH", NS), page_w, m, w)
                y = _vertical(frame.find("wp:positionV", NS), page_h, m, para_top, h)
                front = frame.get("behindDoc") != "1"
            turned = frame.find(".//a:xfrm[@rot]", NS)
            if turned is not None and _int(turned.get("rot")):
                notes.append(f"A turned (rotated) picture in the {where} prints straight.")
            crop = frame.find(".//a:srcRect", NS)
            shares = [max(0.0, min(_int(crop.get(k)) / 100000, 0.9)) if crop is not None else 0.0 for k in "ltrb"]
            if shares[0] + shares[2] >= 0.95 or shares[1] + shares[3] >= 0.95:
                shares = [0.0, 0.0, 0.0, 0.0]
            items.append({"data": data, "anchor": anchor, "x": round(x, 2),
                          "y": round(y if anchor == "top" else page_h - (y + h), 2),
                          "w": round(w, 2), "h": round(h, 2), "crop": [round(s, 4) for s in shares],
                          "front": front})

        if paragraph.find(".//w:pict", NS) is not None:
            notes.append(f"A picture or watermark in the {where} uses an older Word format and cannot be read. "
                         "Insert it again as a normal picture (Insert, Picture).")


def _place(align, start, length, size, offset):
    if align == "center":
        return start + (length - size) / 2
    if align in ("right", "outside"):
        return start + length - size
    return start + offset


def _offset(node):
    """(align, offset in mm) from a positionH / positionV element."""
    if node is None:
        return "left", 0.0
    align = node.find("wp:align", NS)
    if align is not None and align.text:
        return align.text.strip(), 0.0
    offset = node.find("wp:posOffset", NS)
    return "offset", _int(offset.text if offset is not None else 0) * EMU_MM


def _horizontal(node, page_w, m, w):
    origin = node.get("relativeFrom", "column") if node is not None else "column"
    area = {"page": (0, page_w), "leftMargin": (0, m["left"]), "rightMargin": (page_w - m["right"], m["right"])}
    start, length = area.get(origin, (m["left"], page_w - m["left"] - m["right"]))   # margin, column, character...
    align, offset = _offset(node)
    return start + offset if align == "offset" else _place(align, start, length, w, 0)


def _vertical(node, page_h, m, para_top, h):
    origin = node.get("relativeFrom", "paragraph") if node is not None else "paragraph"
    area = {"page": (0, page_h), "margin": (m["top"], page_h - m["top"] - m["bottom"]),
            "topMargin": (0, m["top"]), "bottomMargin": (page_h - m["bottom"], m["bottom"])}
    start, length = area.get(origin, (para_top, 0))                                 # paragraph, line
    align, offset = _offset(node)
    if align == "offset":
        return start + offset
    if align == "center":
        return start + (length - h) / 2
    if align == "bottom":
        return start + length - h
    return start


# ---------------------------------------------------------------- a picture of the whole page instead
PAPERS = {"Short bond (Letter)": (215.9, 279.4), "A4": (210.0, 297.0), "Long bond": (215.9, 330.2),
          "Legal": (215.9, 355.6)}


def page_picture(width_px: int, height_px: int):
    """For a picture of the whole blank page: the paper it most likely is (by its shape), as
    (name, width mm, height mm). A landscape picture is treated as the same paper turned."""
    landscape = width_px > height_px
    ratio = max(width_px, height_px) / max(1, min(width_px, height_px))
    name, (w, h) = min(PAPERS.items(), key=lambda kv: abs(kv[1][1] / kv[1][0] - ratio))
    return (name, h, w) if landscape else (name, w, h)


# ---------------------------------------------------------------- a design, ready for the editor
def build_design(kind: str, raw: bytes, file_name: str, store):
    """A design from an uploaded Word file (kind "docx") or picture of the whole page (kind "picture").
    `store(data, mime, width, height)` keeps a picture and returns its number. Returns (design, notes).
    Raises DocxError or pictures.PictureError with a message for the secretary."""
    import pictures                                          # here, so reading a .docx needs no database
    if kind == "docx":
        found = read_docx(raw, file_name)
        if not found["items"]:
            raise DocxError(" ".join(found["notes"]) + " If the design is made of text boxes or shapes, save the "
                            'blank page as a picture (WPS: Export to Image) and use "Picture of the whole page" instead.')
        items = []
        for item in found["items"]:
            try:
                data, mime, width, height = pictures.prepare(item.pop("data"), max_bytes=MAX_MEDIA_BYTES,
                                                              max_side=pictures.DESIGN_MAX_SIDE)
            except pictures.PictureError as e:
                raise DocxError(f"A picture inside the Word file could not be used: {e}") from None
            items.append({**item, "picture": store(data, mime, width, height)})
        margins = found["margins"]
        within = lambda value, low, high: min(max(value, low), high)
        design = {"source": "docx", "file_name": file_name, "page_w": found["page_w"], "page_h": found["page_h"],
                  "items": items, "layer": "word", "header": "show",
                  "top_mm": int(within(round(margins["top"]), 5, 150)),
                  "bottom_mm": int(within(round(margins["bottom"]), 5, 150)),
                  "left_mm": within(margins["left"], 5, 60), "right_mm": within(margins["right"], 5, 60)}
        return design, found["notes"]

    data, mime, width, height = pictures.prepare(raw, max_bytes=pictures.DESIGN_MAX_BYTES,
                                                  max_side=pictures.DESIGN_MAX_SIDE)
    paper, page_w, page_h = page_picture(width, height)
    design = {"source": "picture", "file_name": file_name, "page_w": page_w, "page_h": page_h,
              "items": [{"picture": store(data, mime, width, height), "anchor": "top", "x": 0, "y": 0,
                         "w": page_w, "h": page_h, "crop": [0, 0, 0, 0], "front": False}],
              "layer": "behind", "header": "hide", "top_mm": 45, "bottom_mm": 35, "left_mm": 18, "right_mm": 18}
    notes = [f"The picture has the shape of {paper} paper, so the design is {page_w / 25.4:.1f} x "
             f"{page_h / 25.4:.1f} in. Print it on that paper."]
    if width < pictures.SHARP_WIDTH:
        notes.append(f"The picture is only {width} pixels wide, so it may look blurry when printed. "
                     f"A picture at least {pictures.SHARP_WIDTH:,} pixels wide prints sharply.")
    return design, notes
