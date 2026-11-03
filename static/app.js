// Barangay Queue & Service Management - shared behavior.
// Every feature looks for its own elements first, so one file works on every page.
(() => {
    const $ = (sel, root = document) => root.querySelector(sel);
    const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

    // ---------- Account menu ----------
    const menuBtn = $('#user-menu-btn');
    const menu = $('#user-menu');
    if (menuBtn && menu) {
        const setOpen = (open) => {
            menu.hidden = !open;
            menuBtn.setAttribute('aria-expanded', String(open));
        };
        menuBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            setOpen(menu.hidden);
        });
        document.addEventListener('click', (e) => {
            if (!menu.contains(e.target)) setOpen(false);
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') setOpen(false);
        });
    }

    // ---------- Modals ----------
    // One confirm dialog per page; any [data-confirm] button fills it in.
    const confirmDialog = $('#confirm-dialog');
    document.addEventListener('click', (e) => {
        const trigger = e.target.closest('[data-confirm]');
        if (trigger && confirmDialog) {
            e.preventDefault();
            $('#confirm-form').action = trigger.dataset.action;
            $('#confirm-title').textContent = trigger.dataset.title || 'Are you sure?';
            $('#confirm-message').textContent = trigger.dataset.message || '';
            const submit = $('#confirm-submit');
            submit.textContent = trigger.dataset.label || 'Confirm';
            const variant = trigger.dataset.variant;
            submit.className = 'btn ' + (variant === 'danger' ? 'btn-danger' : variant === 'success' ? 'btn-success' : 'btn-primary');
            confirmDialog.showModal();
            // Enter confirms a routine action (e.g. Mark complete). For a destructive one the focus
            // stays on Cancel, so a stray Enter never deletes anything.
            (variant === 'danger' ? $('[data-close]', confirmDialog) : submit).focus();
            return;
        }

        const opener = e.target.closest('[data-open-modal]');
        if (opener) {
            const target = document.getElementById(opener.dataset.openModal);
            if (target) target.showModal();
            return;
        }

        const closer = e.target.closest('[data-close]');
        if (closer) {
            closer.closest('dialog').close();
            return;
        }

        // A click on the dim backdrop lands on the <dialog> element itself
        if (e.target instanceof HTMLDialogElement && e.target.open && !e.target.hasAttribute('data-keep-open')) e.target.close();
    });

    // Edit service: one dialog, filled with the row's values
    const editDialog = $('#edit-modal');
    if (editDialog) {
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-edit-service]');
            if (!btn) return;
            $('#edit-form').action = '/services/edit/' + btn.dataset.id;
            $('#edit-name').value = btn.dataset.name;
            $('#edit-status').value = btn.dataset.status;
            const docChoice = $('#edit-template');
            if (docChoice) docChoice.value = btn.dataset.template || ''; 
            editDialog.showModal();
        });
    }

    // ---------- Password reveal switch ----------
    $$('.pw-toggle').forEach((toggle) => {
        toggle.addEventListener('change', () => {
            $$('input[data-pw]', toggle.closest('form')).forEach((input) => {
                input.type = toggle.checked ? 'text' : 'password';
            });
        });
    });

    // ---------- Double-click guard ----------
    // After a POST form is submitted once, ignore further submits and grey out its buttons, so a
    // double click cannot register two tickets or finish the same ticket twice. This covers every
    // POST form, including the shared confirm dialog.
    const UNLOCK_AFTER_MS = 15000;   // safety net in case the page never navigates away

    const lockForm = (form) => {
        form.dataset.submitting = '1';
        // Disable on the next tick: disabling inside the submit event can cancel the submit itself
        setTimeout(() => {
            $$('button', form).forEach((btn) => {
                if (!btn.disabled) {
                    btn.disabled = true;
                    btn.dataset.lockedByGuard = '1';   // remember which ones WE disabled
                }
            });
        }, 0);
        setTimeout(() => unlockForm(form), UNLOCK_AFTER_MS);
    };

    const unlockForm = (form) => {
        delete form.dataset.submitting;
        $$('button[data-locked-by-guard]', form).forEach((btn) => {
            btn.disabled = false;   // only the buttons the guard disabled, never ones the page disabled
            delete btn.dataset.lockedByGuard;
        });
    };

    document.addEventListener('submit', (e) => {
        const form = e.target;
        if (!(form instanceof HTMLFormElement) || form.method.toLowerCase() !== 'post') return;
        if (form.hasAttribute('data-no-lock')) return;
        if (form.hasAttribute('data-needs-confirm') && !form.dataset.confirmed) return;
        if (form.dataset.submitting) {
            e.preventDefault();   // second click / second Enter press: ignore it
            return;
        }
        lockForm(form);
    }, true);

    // Pressing the browser's Back button can restore the page frozen with locked buttons
    window.addEventListener('pageshow', (e) => {
        if (e.persisted) $$('form[data-submitting]').forEach(unlockForm);
    });

    // ---------- "I saved this" checkbox unlocks a link ----------
    $$('[data-unlocks]').forEach((box) => {
        const link = document.getElementById(box.dataset.unlocks);
        if (!link) return;
        box.addEventListener('change', () => {
            link.classList.toggle('is-disabled', !box.checked);
            link.setAttribute('aria-disabled', String(!box.checked));
            link.tabIndex = box.checked ? 0 : -1;
        });
    });

    // ---------- Queue tabs ----------
    const tabs = $$('.tab-btn');
    if (tabs.length) {
        const rows = $$('tr.ticket-row');
        const emptyRow = $('#tab-empty');
        tabs.forEach((tab) => {
            tab.addEventListener('click', () => {
                let shown = 0;
                tabs.forEach((t) => {
                    t.classList.toggle('active', t === tab);
                    t.setAttribute('aria-selected', String(t === tab));
                });
                rows.forEach((row) => {
                    const show = tab.dataset.status === 'all' || row.dataset.status === tab.dataset.status;
                    row.hidden = !show;
                    if (show) shown += 1;
                });
                if (emptyRow) emptyRow.hidden = shown !== 0;
            });
        });
    }

    // ---------- Attendance filters: update without an Apply button ----------
    const filterForm = $('#filter-form');
    if (filterForm) {
        $$('input[type="date"], select', filterForm).forEach((el) => {
            el.addEventListener('change', () => filterForm.submit());
        });
        const search = $('#search-client');
        let timer;
        search.addEventListener('input', () => {
            clearTimeout(timer);
            timer = setTimeout(() => filterForm.submit(), 400);
        });
        // The page reloads on each search; put the cursor back where it was
        if (search.value) {
            search.focus();
            search.setSelectionRange(search.value.length, search.value.length);
        }
    }

    // ---------- Toasts ----------
    // Success messages fade after a few seconds (the timer pauses while the pointer is over them);
    // errors stay until closed.
    const TOAST_MS = 6000;
    const dismissToast = (toast) => {
        toast.classList.add('is-leaving');
        setTimeout(() => toast.remove(), 220);
    };
    $$('.toast').forEach((toast) => {
        $('.toast-close', toast)?.addEventListener('click', () => dismissToast(toast));
        if (!toast.hasAttribute('data-autohide')) return;
        let timer = setTimeout(() => dismissToast(toast), TOAST_MS);
        toast.addEventListener('mouseenter', () => clearTimeout(timer));
        toast.addEventListener('mouseleave', () => { timer = setTimeout(() => dismissToast(toast), TOAST_MS / 2); });
    });

    // ---------- Keyboard shortcuts ----------
    // A button with data-shortcut="n" is pressed by the N key. Ignored while typing, while a dialog
    // is open, or with Ctrl/Alt/Cmd held, so normal typing and browser shortcuts are untouched.
    document.addEventListener('keydown', (e) => {
        if (e.ctrlKey || e.altKey || e.metaKey || e.repeat) return;
        const el = e.target;
        if (el.closest && el.closest('input, textarea, select, [contenteditable="true"]')) return;
        if (document.querySelector('dialog[open]')) return;
        const btn = document.querySelector(`[data-shortcut="${CSS.escape(e.key.toLowerCase())}"]`);
        if (!btn || btn.disabled || btn.closest('[hidden]')) return;
        e.preventDefault();
        btn.click();
    });

    // ---------- Elapsed time ("waiting 12 min") ----------
    // The server writes local times without a zone; the browser runs on the same PC, so they match.
    const LONG_WAIT_MIN = 30;
    const sinceEls = $$('[data-since]');
    const formatSince = (mins) => {
        if (mins < 1) return 'under 1 min';
        if (mins < 60) return mins + ' min';
        const h = Math.floor(mins / 60);
        const m = mins % 60;
        return h + ' h' + (m ? ' ' + m + ' min' : '');
    };
    const tickSince = () => {
        const now = Date.now();
        sinceEls.forEach((el) => {
            const start = new Date(el.dataset.since).getTime();
            if (Number.isNaN(start)) return;
            const mins = Math.max(0, Math.floor((now - start) / 60000));
            el.textContent = (el.dataset.prefix || '') + formatSince(mins);
            el.classList.toggle('is-long', mins >= LONG_WAIT_MIN);
        });
    };
    if (sinceEls.length) {
        tickSince();
        setInterval(tickSince, 30000);
    }

    // ---------- Attendance: quick date ranges ----------
    // Each button fills From and To and reloads; the button matching the current dates is marked.
    const isoDate = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    const rangeFor = (name) => {
        const today = new Date();
        let from = new Date(today);
        let to = new Date(today);
        if (name === 'yesterday') {
            from.setDate(today.getDate() - 1);
            to = new Date(from);
        } else if (name === 'week') {
            from.setDate(today.getDate() - 6);
        } else if (name === 'month') {
            from = new Date(today.getFullYear(), today.getMonth(), 1);
        }
        return [isoDate(from), isoDate(to)];
    };
    $$('[data-range]').forEach((btn) => {
        const form = btn.closest('form');
        const [from, to] = rangeFor(btn.dataset.range);
        btn.setAttribute('aria-pressed', String(form.elements['date_from'].value === from && form.elements['date_to'].value === to));
        btn.addEventListener('click', () => {
            form.elements['date_from'].value = from;
            form.elements['date_to'].value = to;
            form.submit();
        });
    });

    // ---------- Services donut chart ----------
    const donut = $('#donut');
    const dataEl = $('#donut-data');
    if (donut && dataEl) {
        const palette = ['#ff6a00', '#1e293b', '#2563eb', '#16a34a', '#d97706', '#9333ea', '#0d9488', '#dc2626'];
        const data = JSON.parse(dataEl.textContent).map((d, i) => ({ ...d, color: palette[i % palette.length] }));
        const total = data.reduce((sum, d) => sum + d.count, 0);
        const svg = $('svg', donut);
        const centerNum = $('.donut-num', donut);
        const centerLabel = $('.donut-label', donut);
        const legend = $('#donut-legend');
        const NS = 'http://www.w3.org/2000/svg';
        const R = 70;
        const C = 2 * Math.PI * R;

        const circle = (stroke) => {
            const c = document.createElementNS(NS, 'circle');
            c.setAttribute('cx', '100');
            c.setAttribute('cy', '100');
            c.setAttribute('r', String(R));
            c.setAttribute('fill', 'none');
            c.setAttribute('stroke', stroke);
            c.setAttribute('stroke-width', '22');
            c.setAttribute('transform', 'rotate(-90 100 100)');
            return c;
        };
        svg.appendChild(circle('#e2e8f0'));

        const items = [];
        const showTotal = () => {
            centerNum.textContent = String(total);
            centerLabel.textContent = 'Tickets today';
        };
        const setActive = (index) => {
            if (index === null) {
                items.forEach((it) => {
                    if (it.seg) it.seg.classList.remove('is-active', 'is-dim');
                    it.row.classList.remove('is-active');
                });
                showTotal();
                return;
            }
            items.forEach((it, i) => {
                const on = i === index;
                if (it.seg) {
                    it.seg.classList.toggle('is-active', on);
                    it.seg.classList.toggle('is-dim', !on);
                }
                it.row.classList.toggle('is-active', on);
            });
            centerNum.textContent = items[index].pct + '%';
            centerLabel.textContent = items[index].name;
        };

        let offset = 0;
        const gap = data.filter((d) => d.count > 0).length > 1 ? 3 : 0;
        data.forEach((d, i) => {
            const pct = total ? Math.round((d.count / total) * 100) : 0;
            let seg = null;
            if (d.count > 0) {
                const len = (d.count / total) * C;
                seg = circle(d.color);
                seg.classList.add('donut-seg');
                seg.setAttribute('stroke-dasharray', Math.max(len - gap, 0.5) + ' ' + C);
                seg.setAttribute('stroke-dashoffset', String(-offset));
                svg.appendChild(seg);
                offset += len;
            }

            const row = document.createElement('li');
            row.className = 'legend-row' + (d.count === 0 ? ' is-zero' : '');
            row.tabIndex = d.count > 0 ? 0 : -1;
            row.innerHTML = '<span class="legend-swatch"></span><span class="legend-name"></span><span class="legend-count"></span><span class="legend-pct"></span>';
            $('.legend-swatch', row).style.background = d.color;
            $('.legend-name', row).textContent = d.name;
            $('.legend-count', row).textContent = d.count;
            $('.legend-pct', row).textContent = pct + '%';
            legend.appendChild(row);

            items.push({ seg, row, name: d.name, pct });
            if (d.count > 0) {
                [seg, row].forEach((el) => {
                    el.addEventListener('mouseenter', () => setActive(i));
                    el.addEventListener('mouseleave', () => setActive(null));
                });
                row.addEventListener('focus', () => setActive(i));
                row.addEventListener('blur', () => setActive(null));
            }
        });

        if (!data.length) {
            const li = document.createElement('li');
            li.className = 'empty-note';
            li.textContent = 'No services yet.';
            legend.appendChild(li);
        }
        showTotal();
        
    }
})();

