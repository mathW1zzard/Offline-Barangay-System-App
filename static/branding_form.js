// Shared by the Settings page and the first-run wizard: the live header preview and the logo slots.
// Every part looks for its own elements first, so the same file works on every page that includes it.
(() => {
    const $ = (sel, root = document) => root.querySelector(sel);
    const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

    // ---------- Live header preview (text) ----------
    const pv = { line1: $('#pv-line1'), place: $('#pv-place'), name: $('#pv-name'), office: $('#pv-office'),
                 left: $('#pv-left'), right: $('#pv-right'), third: $('#pv-third') };
    const profileForm = $('#form-profile');

    // ---------- The colour of the office line: ready colours or her own, kept in a hidden box ----------
    $$('[data-color-choice]').forEach((box) => {
        const kept = $('input[name="office_color"]', box);
        const own = $('[data-color-own]', box);
        const show = () => {                          // the buttons follow the hidden box
            const value = kept.value.toUpperCase();
            let ready = false;
            $$('[data-color]', box).forEach((button) => {
                const on = button.dataset.color.toUpperCase() === value;
                ready = ready || on;
                button.setAttribute('aria-checked', on ? 'true' : 'false');
            });
            own.closest('.color-own').classList.toggle('is-on', !ready);
            if (!ready && /^#[0-9A-F]{6}$/.test(value)) own.value = value.toLowerCase();
        };
        const choose = (value) => {
            kept.value = value.toUpperCase();
            kept.dispatchEvent(new Event('input', { bubbles: true }));    // the preview and "unsaved" follow
        };
        $$('[data-color]', box).forEach((button) => button.addEventListener('click', () => choose(button.dataset.color)));
        own.addEventListener('input', () => choose(own.value));
        kept.addEventListener('input', show);          // also when Discard puts the saved colour back
        show();
    });

    if (profileForm && pv.name) {
        const typed = (name) => $('[name="' + name + '"]', profileForm).value.trim().replace(/\s+/g, ' ');
        const updateText = () => {
            pv.line1.textContent = typed('header_line_1');
            pv.place.textContent = [typed('municipality'), typed('province')].filter(Boolean).join(', ').toUpperCase();
            pv.name.textContent = (typed('barangay_name') || 'Barangay name not set').toUpperCase();
            pv.office.textContent = typed('header_line_2').toUpperCase();
            const color = $('[name="office_color"]', profileForm);
            if (color && /^#[0-9A-F]{6}$/i.test(color.value)) {
                pv.name.closest('.doc-head-box').style.setProperty('--office-color', color.value);
            }
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