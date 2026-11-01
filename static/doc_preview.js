// Draws the paragraphs the server sends for a live preview (Templates editor and issue form).
// Each paragraph is a list of [text, bold] pieces; a piece that is exactly a Tab is a fixed gap.
// Text only: nothing typed can ever become markup.
window.drawDocParagraphs = function (container, paragraphs) {
    container.replaceChildren(...paragraphs.map((pairs) => {
        const p = document.createElement('p');
        pairs.forEach(([text, bold]) => {
            if (text === '\t') {
                const gap = document.createElement('span');
                gap.className = 'tab';
                p.appendChild(gap);
                return;
            }
            const piece = document.createElement(bold ? 'strong' : 'span');
            piece.textContent = text;
            p.appendChild(piece);
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