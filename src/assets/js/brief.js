// Research brief pages: the Copy Link and Print buttons. Same behavior as on the heat map reports.

const copy = document.getElementById('copyLink');
const label = copy && copy.querySelector('span');
if (copy && navigator.clipboard) {
  let reset;
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(location.href);
      label.textContent = 'Link Copied';
    } catch {
      label.textContent = 'Copy Failed';
    }
    clearTimeout(reset);
    reset = setTimeout(() => { label.textContent = 'Copy Link'; }, 2000);
  });
} else if (copy) {
  copy.hidden = true;
}

const print = document.getElementById('printReport');
if (print) print.addEventListener('click', () => window.print());
