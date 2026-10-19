/*
 * Bookmarklet source: run on a Kobo blog 一週99 (weekly-dd99-*) or 本週精選 (weekly-8585-*) post.
 * Extracts raw book blocks and downloads them as <slug>.json (clipboard writes fail when the
 * page is unfocused, which is common for bookmarklets); src/ingest.py does all parsing/validation.
 * Build the javascript: URL with `python3 scripts/build_bookmarklet.py`.
 * Do not use line comments below this header: the build collapses whitespace.
 */
(() => {
  const text = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');
  const publisherFromText = (el) => {
    const match = text(el.querySelector('p')).match(/出版社[:：]\s*(.+?)\s*(查看電子書|$)/);
    return match ? match[1] : '';
  };
  const items = [];
  let intro = '';
  let pending = null;

  document.querySelectorAll('.content-block, .book-block').forEach((el) => {
    if (el.classList.contains('content-block')) {
      const heading = text(el.querySelector('h3'));
      if (!heading) {
        intro = intro || text(el);
        return;
      }
      pending = { heading, description: text(el.querySelector('p')) };
      return;
    }
    const hk = [...el.querySelectorAll('a')].find((a) => /HK/.test(a.textContent));
    items.push({
      heading: pending ? pending.heading : '',
      description: pending ? pending.description : '',
      title: text(el.querySelector('.title')),
      author: text(el.querySelector('.author')),
      publisher: text(el.querySelector('a[href*="Imprint"]')) || publisherFromText(el),
      kobo_url: (el.querySelector('.book-block__link') || {}).href || '',
      kobo_hk_url: hk ? hk.href : '',
      cover_url: (el.querySelector('img') || {}).src || '',
    });
    pending = null;
  });

  if (!/\/blog\/weekly-(dd99|8585)-/.test(location.pathname) || !items.length) {
    alert('找不到書單：請在 Kobo blog 的「一週99書單」或「本週精選書單」文章頁使用。');
    return;
  }

  const slug = location.pathname.split('/').filter(Boolean).pop();
  const json = JSON.stringify({
    source: 'kobo-blog-bookmarklet',
    version: 1,
    url: location.origin + location.pathname,
    title: text(document.querySelector('h1')),
    intro,
    items,
  });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([json], { type: 'application/json' }));
  link.download = slug + '.json';
  document.body.appendChild(link);
  link.click();
  link.remove();
  alert('已下載 ' + slug + '.json（' + items.length + ' 本書）。\n請執行：~/workspace/kobo99/scripts/update.sh');
})();
