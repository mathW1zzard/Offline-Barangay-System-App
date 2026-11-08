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

    // ---------- Light / dark menu ----------
    // The choice is applied at once by changing <html data-theme>, then saved by the server so it
    // survives restarts. If saving fails the new look stays for now and a short note says so.
    const themeBtn = $('#theme-btn');
    const themeMenu = $('#theme-options');
    if (themeBtn && themeMenu) {
        const themeError = $('#theme-error');
        const choices = $$('[data-theme-choice]', themeMenu);
        const setThemeOpen = (open) => {
            themeMenu.hidden = !open;
            themeBtn.setAttribute('aria-expanded', String(open));
            if (open) (choices.find((c) => c.getAttribute('aria-checked') === 'true') || choices[0]).focus();
        };
        themeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            setThemeOpen(themeMenu.hidden);
        });
        document.addEventListener('click', (e) => {
            if (!themeMenu.hidden && !themeMenu.contains(e.target)) setThemeOpen(false);
        });
        themeMenu.addEventListener('keydown', (e) => {
            const i = choices.indexOf(document.activeElement);
            if (e.key === 'Escape') { setThemeOpen(false); themeBtn.focus(); }
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                const step = e.key === 'ArrowDown' ? 1 : -1;
                choices[(i + step + choices.length) % choices.length].focus();
            }
        });
        choices.forEach((choice) => {
            choice.addEventListener('click', async () => {
                const theme = choice.dataset.themeChoice;
                document.documentElement.setAttribute('data-theme', theme);
                choices.forEach((c) => c.setAttribute('aria-checked', String(c === choice)));
                themeError.hidden = true;
                try {
                    const res = await fetch('/preferences/theme', {
                        method: 'POST',
                        body: new URLSearchParams({ theme }),
                        headers: { Accept: 'application/json' },
                    });
                    const json = (res.headers.get('content-type') || '').includes('application/json');
                    if (!res.ok || !json) throw new Error('not saved');
                    setThemeOpen(false);
                    themeBtn.focus();
                } catch (err) {
                    themeError.textContent = 'Changed for now, but not saved. It goes back after a restart.';
                    themeError.hidden = false;
                }
            });
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

    // Edit staff account: one dialog, filled with the row's values (step 9b)
    const staffDialog = $('#edit-staff-modal');
    if (staffDialog) {
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-edit-staff]');
            if (!btn) return;
            $('#edit-staff-form').action = '/staff/' + btn.dataset.id + '/edit';
            $('#edit-staff-username').textContent = btn.dataset.username;
            $('#edit-full-name').value = btn.dataset.name;
            const role = $('#edit-role');
            role.value = btn.dataset.role;
            // Your own Administrator role stays: only that choice is offered
            const lockSelf = Boolean(btn.dataset.self && btn.dataset.admin);
            $$('option', role).forEach((o) => { o.disabled = lockSelf && !o.dataset.adminRole; });
            $('#edit-role-self').hidden = !lockSelf;
            staffDialog.showModal();
        });
    }

    // ---------- Role pop-ups (Staff accounts > Roles) ----------
    // Add OR and Void need View issued documents (the server enforces this too)
    document.addEventListener('change', (e) => {
        const box = e.target;
        if (!box.matches || !box.matches('[data-role-form] input[name="perm"]')) return;
        const form = box.closest('form');
        if (box.checked && box.dataset.needs) {
            const needed = $(`input[name="perm"][value="${box.dataset.needs}"]`, form);
            if (needed) needed.checked = true;
        }
        if (!box.checked) {
            $$(`input[name="perm"][data-needs="${box.value}"]`, form).forEach((d) => { d.checked = false; });
        }
    });

    $$('[data-role-form]').forEach((form) => {
        const error = $('[data-role-error]', form);
        const showError = (text) => {
            error.textContent = text;
            error.hidden = false;
            error.scrollIntoView({ block: 'nearest' });
        };
        // Say what's missing before sending, so the pop-up stays open with the ticks in place
        form.addEventListener('submit', (e) => {
            if (!form.elements.name.value.trim()) {
                e.preventDefault();
                showError('Give the role a name, for example Front desk or Treasurer.');
                form.elements.name.focus();
            } else if (!$('input[name="perm"]:checked', form)) {
                e.preventDefault();
                showError('Tick at least one thing this role may do.');
            }
        });
        // Cancel (or Esc) forgets unsaved changes: next time it opens as saved
        const dialog = form.closest('dialog');
        dialog.addEventListener('close', () => {
            if (dialog.hasAttribute('data-refused')) {     // came back refused: show the saved roles again
                location.replace('/staff/roles');
                return;
            }
            form.reset();
            error.hidden = true;
        });
    });

    // A save the server refused comes back with its pop-up open again
    const reopen = $('dialog[data-open-on-load]');
    if (reopen) reopen.showModal();

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
        form.dataset.submitting = '1';
        setTimeout(() => {
            if (e.defaultPrevented) delete form.dataset.submitting;
            else lockForm(form);
        }, 0);
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
        const palette = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)', 'var(--chart-6)', 'var(--chart-7)', 'var(--chart-8)'];
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
            c.style.stroke = stroke;                  // a style, so CSS variables work
            c.setAttribute('stroke-width', '22');
            c.setAttribute('transform', 'rotate(-90 100 100)');
            return c;
        };
        svg.appendChild(circle('var(--chart-track)'));

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

    // ---------- Idle logout (step 9c) ----------
    // Shared front-desk PC: after 9 minutes with no clicking or typing a pop-up counts down from 60,
    // then the page logs out by itself. While someone is active, the server is told about once every
    // 30 seconds, so a long form typed without opening another page never counts as "idle" there.
    const idleMinutes = parseFloat(document.body.dataset.idleMinutes || '0');
    const idleDialog = $('#idle-dialog');
    if (idleMinutes > 0 && idleDialog) {
        const LIMIT_MS = idleMinutes * 60 * 1000;
        const WARN_MS = parseFloat(document.body.dataset.idleWarning || '60') * 1000;
        const PING_EVERY_MS = Math.min(30 * 1000, LIMIT_MS / 4);
        const countEl = $('#idle-count');
        let lastActive = Date.now();
        let lastPing = Date.now();          // opening this page already counted as activity
        let leaving = false;
        let askedAt = 0;                    // when the server last said how long nobody has been active
        let asking = false;                 // waiting for that answer: decide nothing until it comes

        const tellServer = () => {
            lastPing = Date.now();
            fetch('/still-here', { method: 'POST' })
                .then((res) => { if (res.status === 401) { leaving = true; location.href = '/login'; } })
                .catch(() => {});           // the app is closing or restarting: nothing to do
        };

        // While the pop-up is open, only "I'm still here" (or Esc) answers it. Otherwise moving the
        // mouse toward a button would close the pop-up before the person could click it.
        const stillHere = (e) => {
            if (leaving) return;
            if (idleDialog.open && e && e.currentTarget === document) return;
            lastActive = Date.now();
            if (idleDialog.open) idleDialog.close();
            if (Date.now() - lastPing >= PING_EVERY_MS) tellServer();
        };

        ['pointerdown', 'pointermove', 'keydown', 'wheel', 'scroll', 'touchstart', 'input'].forEach((name) =>
            document.addEventListener(name, stillHere, { capture: true, passive: true }));
        $('#idle-stay').addEventListener('click', stillHere);
        idleDialog.addEventListener('cancel', (e) => { e.preventDefault(); stillHere(); });   // Esc = still here

        // Before warning or logging out, check with the server. This page's own clock can be wrong:
        // pressing Back can bring it back from the browser's memory still counting from when the person
        // left it, while they were busy on another page the whole time. The server saw that activity.
        const askServer = () => {
            asking = true;
            fetch('/still-here', { method: 'POST', body: new URLSearchParams({ only_check: '1' }) })
                .then((res) => {
                    if (res.status === 401) { leaving = true; location.href = '/login'; return null; }
                    return res.json();
                })
                .then((data) => {
                    if (!data || leaving) return;
                    const serverIdleMs = data.idle_seconds * 1000;
                    if (serverIdleMs < Date.now() - lastActive) {        // active elsewhere: catch up
                        lastActive = Date.now() - serverIdleMs;
                        if (Date.now() - lastActive < LIMIT_MS - WARN_MS && idleDialog.open) idleDialog.close();
                    }
                })
                .catch(() => {})            // no answer (app closing): this page's own clock decides
                .finally(() => { asking = false; askedAt = Date.now(); });
        };

        setInterval(() => {
            if (leaving || asking) return;
            const idle = Date.now() - lastActive;
            if (idle >= LIMIT_MS - WARN_MS && Date.now() - askedAt > WARN_MS) {
                askServer();                // decide on the next tick, with the server's answer
                return;
            }
            if (idle >= LIMIT_MS) {          // also true right after the PC wakes from sleep
                leaving = true;
                $('#idle-logout-form').submit();
                return;
            }
            if (idle >= LIMIT_MS - WARN_MS) {
                countEl.textContent = Math.max(1, Math.ceil((LIMIT_MS - idle) / 1000));
                if (!idleDialog.open) {
                    idleDialog.showModal();
                    $('#idle-stay').focus();
                }
            }
        }, 1000);
    }
})();

