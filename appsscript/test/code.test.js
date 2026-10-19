// Run: node --test appsscript/test
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const code = require(path.join(__dirname, '..', 'Code.gs'));

const CDN = (id) => `https://cdn.kobo.com/book-images/abc-123/1200/1200/False/${id}.jpg`;
const BAR = (n) => `https://shopjp.kobo.com/assets/x/20261015_dailydeal/images/bar${n}.jpg`;
const TRACK = 'https://shopjp.kobo.com/pub/cc?_ri_=SECRET&amp;x=1';
const FROM = '樂天Kobo <news@shopjp.kobo.com>';

function bookHtml(n, id, title, listPrice, sell, desc = '') {
  return `<a id="book${n}-img-product-link" href="${TRACK}"><img id="book${n}-img-image-link" src="${CDN(id)}" width="150"></a>`
    + `<p style="x"><a id="book${n}-title" href="${TRACK}" style="y">${title}</a></p>`
    + `<p>優惠價 $ <del id="book${n}-list-price">${listPrice}</del></p>`
    + `<p id="book${n}-sell-price" style="z">${sell}</p>`
    + (desc ? `<p id="book${n}-description">${desc}</p>` : '');
}

const SELECT = {
  subject: '【本週精選】曉星',
  from: FROM,
  sentMs: Date.parse('2026-10-19T02:00:20Z'),
  html: '<span>10 本夯書限時85折優惠起，輸入WKSelect1019每本再享85折</span>'
    + bookHtml(1, 'HB7tVogQOzeycgEGzy4cgw', '不懂權謀的人&amp;測試', '255', '85折價格$ 217', '職場&nbsp;險惡')
    + bookHtml(2, 'ai-627', '超級代理效應', '1,319', '85折價格$ 1,121'),
};

const DD99_HTML = '<span>10/15-10/21 一週 99 元完整書單搶先看</span>'
  + `<img src="${BAR(1)}" width="640">` + bookHtml(1, 'ai-146', '李飛飛', '$450', '特定價$ 99')
  + bookHtml(2, 'zcPp', '超高齡', '$300', '特定價$ 99')
  + `<img src="${BAR(2)}">` + bookHtml(3, 'n1438', '霍金传', '$235', '特定價$ 99');
const DD99 = { subject: '【今日 99】李飛飛', from: FROM, sentMs: Date.parse('2026-10-15T02:00:22Z'), html: DD99_HTML };

const bars = (texts) => {
  const calls = [];
  const read = (urls) => { calls.push(urls); return texts; };
  return { read, calls };
};

test('精選 email: rank, prices, promo, period, entities decoded', () => {
  const list = code.parseEmail(SELECT, () => []);
  assert.equal(list.slug, 'weekly-8585-2026-w43');
  assert.deepEqual([list.period_start, list.period_end], ['2026-10-19', '2026-10-25']);
  assert.equal(list.promo_code, 'WKSelect1019');
  assert.deepEqual(list.books[0], {
    title: '不懂權謀的人&測試',
    description: '職場 險惡',
    kobo_url: 'https://www.kobo.com/tw/zh/ebook/HB7tVogQOzeycgEGzy4cgw',
    cover_url: 'https://cdn.kobo.com/book-images/abc-123/300/300/False/HB7tVogQOzeycgEGzy4cgw.jpg',
    list_price: 255,
    sale_price: 217,
    rank: 1,
  });
  assert.deepEqual([list.books[1].list_price, list.books[1].sale_price], [1319, 1121]);
  assert.ok(!JSON.stringify(list).includes('SECRET'), 'tracking links must not be stored');
});

test('99 email: banner weekdays map to dates incl. the extra Friday', () => {
  const { read, calls } = bars(['-星期四限定 星期五限定-', '- 星期五加碼-']);
  const list = code.parseEmail(DD99, read);
  assert.deepEqual(calls, [[BAR(1), BAR(2)]]);
  assert.equal(list.slug, 'weekly-dd99-2026-w42');
  assert.deepEqual(list.books.map((b) => b.sale_date), ['2026-10-15', '2026-10-16', '2026-10-16']);
  assert.equal(list.books[0].list_price, 450);
});

test('99 email: period across new year, ISO week 53', () => {
  const email = { ...DD99, html: DD99_HTML.replace('10/15-10/21', '12/31-1/6'), sentMs: Date.parse('2026-12-31T02:00:00Z') };
  const list = code.parseEmail(email, () => ['星期四 星期六', '星期三']);
  assert.deepEqual(list.books.map((b) => b.sale_date), ['2026-12-31', '2027-01-02', '2027-01-06']);
  assert.equal(list.slug, 'weekly-dd99-2026-w53');
});

test('99 email: unreadable banner fails loudly', () => {
  assert.throws(() => code.parseEmail(DD99, () => ['星期四', '星期五']), /expected 2 weekdays/);
  assert.throws(() => code.parseEmail(DD99, () => ['??', '??']), /expected 2 weekdays/);
});

test('99 email: missing banners / period / unexpected banner host', () => {
  assert.throws(() => code.parseEmail({ ...DD99, html: DD99_HTML.replace(/bar(\d)/g, 'pic$1') }, () => []), /banners/);
  assert.throws(() => code.parseEmail({ ...DD99, html: DD99_HTML.replace('10/15-10/21', '') }, () => []), /period/);
  const evil = DD99_HTML.replace(/https:\/\/shopjp\.kobo\.com\/assets/g, 'https://evil.example/assets');
  assert.throws(() => code.parseEmail({ ...DD99, html: evil }, () => ['星期四 星期五', '星期五']), /host/);
});

test('rejects non-Kobo, look-alike senders, other subjects, empty or broken templates', () => {
  assert.throws(() => code.parseEmail({ ...SELECT, from: 'x <news@kobo.com.evil.example>' }, () => []), /Not a Kobo/);
  assert.throws(() => code.parseEmail({ ...SELECT, subject: 'Your receipt' }, () => []), /Not a Kobo/);
  assert.throws(() => code.parseEmail({ ...SELECT, html: '<p>none</p>' }, () => []), /No books/);
  assert.throws(() => code.parseEmail({ ...SELECT, html: SELECT.html.replace(/cdn\.kobo\.com/g, 'evil.example') }, () => []), /cover/);
  assert.throws(() => code.parseEmail({ ...SELECT, html: '' }, () => []), /no HTML/);
});

test('latestPerKind keeps the newest of each kind and ignores other mail', () => {
  const older = { ...SELECT, subject: '【本週精選】old', sentMs: SELECT.sentMs - 7 * 86400000 };
  const other = { ...SELECT, subject: 'Kobo 書展', sentMs: SELECT.sentMs + 86400000 };
  const latest = code.latestPerKind([older, SELECT, DD99, other]);
  assert.deepEqual(Object.keys(latest).sort(), ['8585', 'dd99']);
  assert.equal(latest['8585'], SELECT);
});

test('date helpers', () => {
  assert.equal(code.taipeiDate(Date.parse('2026-10-18T16:30:00Z')), '2026-10-19');
  assert.equal(code.weekday('2026-10-19'), 0);
  assert.deepEqual(code.isoWeek('2026-10-15'), { year: 2026, week: 42 });
  assert.deepEqual(code.isoWeek('2027-01-01'), { year: 2026, week: 53 });
  assert.deepEqual(code.isoWeek('2026-12-28'), { year: 2026, week: 53 });
});

const BLOG_BOOK = {
  title: '不懂權謀的人，無法做大事：馬基維利教你如何在職場上出人頭地',
  author: '馬克．鮑威爾',
  description: '完整的部落格介紹文字',
  kobo_url: 'https://www.kobo.com/tw/zh/ebook/HB7?utm_source=twblog',
  rank: 1,
};
const EMAIL_BOOK = { title: '不懂權謀的人，無法做大事', description: '', kobo_url: 'https://www.kobo.com/tw/zh/ebook/HB7', list_price: 255, sale_price: 217, rank: 1 };
const list = (books, extra = {}) => ({
  slug: 'weekly-8585-2026-w43', kind: '8585', label: '本週精選書單', title: 'blog title',
  period_start: '2026-10-19', period_end: '2026-10-25', promo_code: null, books, ...extra,
});

test('merge: email after blog adds prices, keeps richer text and blog title', () => {
  const merged = code.mergeLists(list([BLOG_BOOK]), list([EMAIL_BOOK], { title: 'subject', promo_code: 'WK1' }));
  const [book] = merged.books;
  assert.equal(book.title, BLOG_BOOK.title);
  assert.equal(book.author, '馬克．鮑威爾');
  assert.equal(book.description, '完整的部落格介紹文字');
  assert.deepEqual([book.list_price, book.sale_price], [255, 217]);
  assert.equal(merged.title, 'blog title');
  assert.equal(merged.promo_code, 'WK1');
});

test('merge: unmatched old books kept, inputs not mutated, idempotent', () => {
  const other = { ...BLOG_BOOK, kobo_url: 'https://www.kobo.com/tw/zh/ebook/OTHER' };
  const oldList = list([other]);
  const newList = list([EMAIL_BOOK]);
  const merged = code.mergeLists(oldList, newList);
  assert.deepEqual(merged.books.map((b) => code.ebookId(b.kobo_url)), ['HB7', 'OTHER']);
  assert.deepEqual(oldList.books, [other]);
  assert.deepEqual(code.mergeLists(merged, newList), merged);
  assert.equal(code.mergeLists(null, newList), newList);
});

test('manifest: replace same slug, newest first', () => {
  const dd99 = { ...list([]), slug: 'weekly-dd99-2026-w42', kind: 'dd99', period_start: '2026-10-15' };
  let manifest = code.updateManifest({}, dd99);
  manifest = code.updateManifest(manifest, list([]));
  manifest = code.updateManifest(manifest, dd99);
  assert.deepEqual(manifest.lists.map((e) => e.slug), ['weekly-8585-2026-w43', 'weekly-dd99-2026-w42']);
  assert.deepEqual(Object.keys(manifest.lists[0]), ['slug', 'kind', 'label', 'title', 'period_start', 'period_end']);
});
