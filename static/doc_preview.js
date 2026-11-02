// Draws the paragraphs the server sends for a live preview (Templates editor and issue form).
// Each paragraph is a list of [text, bold] pieces; a piece that is exactly a Tab is a fixed gap.
// Text only: nothing typed can ever become markup.
// A piece that is exactly a line break starts a new line; a centre / right sign at the start of a
// line places that line (the same as cert_body in _macros.html).
window.drawDocParagraphs = function (container, paragraphs) {
    container.replaceChildren(...paragraphs.map((pairs) => {
        const p = document.createElement('p');
        let line = null;
        const newLine = () => {
            line = document.createElement('span');
            line.className = 'ln ln-left';
            p.appendChild(line);
        };
        newLine();
        pairs.forEach(([text, bold]) => {
            if (text === '\n') { newLine(); return; }
            if (text === '\x01center' || text === '\x01right') {
                if (!line.childNodes.length) line.className = 'ln ln-' + text.slice(1);
                return;
            }
            if (text === '\t') {
                const gap = document.createElement('span');
                gap.className = 'tab';
                line.appendChild(gap);
                return;
            }
            const piece = document.createElement(bold ? 'strong' : 'span');
            piece.textContent = text;
            line.appendChild(piece);
        });
        return p;
    }));
};

// Shows the signature lines for the chosen signers: "both", "captain" or "secretary".
window.showDocSigners = function (box, choice) {
    box.querySelectorAll('[data-signer]').forEach((line) => {
        line.hidden = !(choice === 'both' || line.dataset.signer === choice);
    });
    box.classList.toggle('single', choice !== 'both');
};

// Puts a design on a preview sheet (made with cert_open and cert_layers(..., ids=True)): the same
// sizes and picture positions as the cert_open / cert_layers macros in _macros.html.
// design: null for a plain page, or {page_w, page_h, top_mm, bottom_mm, left_mm, right_mm, header,
// layer, items: [{picture, anchor, x, y, w, h, crop: [l, t, r, b], front}]}. pt: text size in half points.
window.CERT_PLAIN = { page_w: 215.9, page_h: 279.4, top_mm: 15, bottom_mm: 15, left_mm: 18, right_mm: 18,
                      header: 'show', layer: 'word', items: [] };
window.applyCertDesign = function (sheet, design, pt) {
    const d = design || window.CERT_PLAIN;
    const set = (name, value) => sheet.style.setProperty(name, value);
    set('--cert-w', Number(d.page_w) + 'mm');
    set('--cert-h', Number(d.page_h) + 'mm');
    set('--cert-top', Number(d.top_mm) + 'mm');
    set('--cert-bottom', Number(d.bottom_mm) + 'mm');
    set('--cert-left', Number(d.left_mm) + 'mm');
    set('--cert-right', Number(d.right_mm) + 'mm');
    if (pt) set('--cert-pt', Number(pt) / 2 + 'pt');
    const header = sheet.querySelector('[data-cert-header]');
    if (header) header.hidden = d.header === 'hide';
    ['behind', 'front'].forEach((layer) => {
        const box = sheet.querySelector('#cert-pictures-' + layer);
        if (!box) return;
        box.replaceChildren(...d.items.filter((item) => {
            const front = d.layer === 'word' ? item.front : d.layer === 'front';
            return front === (layer === 'front');
        }).map((item) => {
            const [l, t, r, b] = item.crop.map(Number);
            const keptW = 1 - l - r, keptH = 1 - t - b;
            const frame = document.createElement('div');
            frame.className = 'cert-picture';
            frame.style.left = Number(item.x) + 'mm';
            frame.style[item.anchor === 'bottom' ? 'bottom' : 'top'] = Number(item.y) + 'mm';
            frame.style.width = Number(item.w) + 'mm';
            frame.style.height = Number(item.h) + 'mm';
            const img = document.createElement('img');
            img.alt = '';
            img.src = '/pictures/' + Number(item.picture);
            img.style.width = (100 / keptW) + '%';
            img.style.height = (100 / keptH) + '%';
            img.style.left = (-100 * l / keptW) + '%';
            img.style.top = (-100 * t / keptH) + '%';
            frame.appendChild(img);
            return frame;
        }));
    });
};

// The previews show the real sheet of paper, shrunk to the width of its frame. It is fitted again
// whenever the frame or the sheet changes size (window resized, text or a picture added).
(() => {
    function fit(frame) {
        const sheet = frame && frame.querySelector('.cert');
        if (!sheet || !sheet.offsetWidth) return;
        const scale = frame.clientWidth / sheet.offsetWidth;
        sheet.style.transform = 'scale(' + scale + ')';
        const height = Math.ceil(sheet.offsetHeight * scale) + 'px';
        if (frame.style.height !== height) frame.style.height = height;
        // Warn when the text runs past the bottom of the design's paper: it would print on two pages
        const note = frame.parentElement.querySelector('[data-overflow-note]');
        if (note) {
            const probe = document.createElement('div');
            probe.style.height = 'var(--cert-h)';
            probe.style.position = 'absolute';
            sheet.appendChild(probe);
            const paper = probe.offsetHeight;
            probe.remove();
            note.hidden = sheet.offsetHeight <= paper + 1;
        }
    }
    window.fitCertFrames = () => document.querySelectorAll('.cert-frame').forEach(fit);
    let pending = false;
    const watch = new ResizeObserver(() => {                 // fit on the next frame: no resize loop
        if (pending) return;
        pending = true;
        requestAnimationFrame(() => { pending = false; window.fitCertFrames(); });
    });
    function start() {
        document.querySelectorAll('.cert-frame').forEach((frame) => {
            watch.observe(frame);
            const sheet = frame.querySelector('.cert');
            if (sheet) watch.observe(sheet);
        });
        window.fitCertFrames();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
