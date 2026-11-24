'use strict';
// Runs one card company's scraper for the Finance app, on your computer only.
// The login comes in on stdin (never on the command line or in the environment) and is never written anywhere.
// The answer goes to stdout after a marker line: transactions only, with no login details in it.

const MARK = '@@FTRACKER-RESULT@@';

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => { data += chunk; });
    process.stdin.on('end', () => resolve(data));
    process.stdin.on('error', reject);
  });
}

function send(obj) {
  process.stdout.write('\n' + MARK + '\n' + JSON.stringify(obj) + '\n');
}

(async () => {
  let out;
  try {
    const { createScraper } = require('israeli-bank-scrapers');
    const moment = require('moment-timezone');
    const day = (iso) => (iso ? moment(iso).tz('Asia/Jerusalem').format('YYYY-MM-DD') : null);
    const req = JSON.parse(await readStdin());
    const credentials = req.credentials;
    req.credentials = null;
    const scraper = createScraper({
      companyId: req.companyId,
      startDate: moment.tz(req.startDate, 'Asia/Jerusalem').toDate(),
      combineInstallments: false,
      showBrowser: !!req.showBrowser,
      executablePath: req.executablePath || undefined,
      defaultTimeout: req.timeoutMs || 60000,
      verbose: false,
    });
    const r = await scraper.scrape(credentials);
    if (!r.success) {
      out = { ok: false, errorType: r.errorType || 'GENERIC', errorMessage: String(r.errorMessage || '').slice(0, 300) };
    } else {
      out = {
        ok: true,
        accounts: (r.accounts || []).map((a) => ({
          accountNumber: String(a.accountNumber || ''),
          txns: (a.txns || []).map((t) => ({
            type: t.type || 'normal',
            identifier: t.identifier == null ? null : String(t.identifier),
            date: day(t.date),
            processedDate: day(t.processedDate),
            originalAmount: t.originalAmount,
            originalCurrency: t.originalCurrency || null,
            chargedAmount: t.chargedAmount,
            chargedCurrency: t.chargedCurrency || null,
            description: t.description || '',
            memo: t.memo || '',
            installments: t.installments || null,
            status: t.status || 'completed',
          })),
        })),
      };
    }
  } catch (e) {
    out = { ok: false, errorType: 'CRASH', errorMessage: String((e && e.message) || e).slice(0, 300) };
  }
  send(out);
  process.exit(0);
})();
