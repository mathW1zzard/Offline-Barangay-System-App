// Shared by the Settings page and the first-run wizard: the live header preview and the logo slots.
// Every part looks for its own elements first, so the same file works on every page that includes it.
(() => {
    const $ = (sel, root = document) => root.querySelector(sel);
    const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

    // ---------- Live header preview (text) ----------
    const pv = { line1: $('#pv-line1'), name: $('#pv-name'), line2: $('#pv-line2'),
                 left: $('#pv-left'), right: $('#pv-right'), third: $('#pv-third') };
    const profileForm = $('#form-profile');
    if (profileForm && pv.name) {
        const typed = (name) => $('[name="' + name + '"]', profileForm).value.trim().replace(/\s+/g, ' ');
        const updateText = () => {
            pv.line1.textContent = typed('header_line_1').toUpperCase();
            const parts = [typed('barangay_name'), typed('municipality'), typed('province')].filter(Boolean);
            pv.name.textContent = (parts.length ? parts.join(', ') : 'Barangay name not set').toUpperCase();
            pv.line2.textContent = typed('header_line_2');
        };
        $$('input', profileForm).forEach((input) => input.addEventListener('input', updateText));
        updateText();
    }

    // ---------- Logo slots: choose, preview, remove (nothing is saved yet) ----------
    const MAX_BYTES = 2 * 1024 * 1024;
    $$('.logo-slot').forEach((slot) => {
        const file = $('input[type="file"]', slot);
        const removeFlag = $('input[type="hidden"]', slot);
        const box = $('.logo-img', slot);
        const empty = $('.logo-empty', slot);
        const message = $('.logo-msg', slot);
        const previewImg = pv[slot.dataset.slot];
        let objectUrl = null;

        const show = (src) => {                       // src = null empties the slot in both previews
            [box, previewImg].forEach((img) => {
                if (!img) return;
                if (src) { img.src = src; img.hidden = false; }
                else { img.removeAttribute('src'); img.hidden = true; }
            });
            empty.hidden = Boolean(src);
        };

        $('[data-logo-choose]', slot).addEventListener('click', () => file.click());

        file.addEventListener('change', () => {
            message.textContent = '';
            const picked = file.files[0];
            if (!picked) return;
            if (picked.size > MAX_BYTES) {
                file.value = '';
                message.textContent = 'That picture is larger than 2 MB. Please choose a smaller one.';
                return;
            }
            if (objectUrl) URL.revokeObjectURL(objectUrl);
            objectUrl = URL.createObjectURL(picked);
            removeFlag.value = '0';
            show(objectUrl);
        });

        $('[data-logo-remove]', slot).addEventListener('click', () => {
            file.value = '';
            removeFlag.value = '1';
            message.textContent = '';
            show(null);
            file.form.dataset.dirty = '1';
        });
    });
})();