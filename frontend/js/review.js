// What will not be in Oracle exactly as the file says, as one compact table: each row is a value and a status chip; the
// full reason is on the chip (tooltip). Used before approval (with the "reviewed" tick) and on the Saved screen.
import { esc, plural } from './util.js';

const TONES = { new: 'info', check: 'warn', skipped: 'muted', converted: 'warn', moved: 'warn' };

// "Sheet “D99001” rows 20–21, columns E–I: 10 numbers not read (under …)" → where | what
function split(text) {
  const i = String(text).indexOf(': ');
  return i > 0 ? [text.slice(0, i), text.slice(i + 2)] : [String(text), ''];
}

const many = n => n > 1;
function quoted(x) {
  if (!x.values?.length) return plural(x.count, 'row');
  const more = x.count > x.values.length ? ` +${x.count - x.values.length}` : '';
  return x.values.join(', ') + more;
}

// groups for an unsaved plan (d.plan.exceptions + warnings) or a save receipt
export function planGroups(d) {
  const ex = d.plan.exceptions || {}; const who = i => many(d.orders.length) ? `Order ${i + 1} · ` : '';
  // The automatic OC No. is shown on the order itself ("Assigned when saved"), so its note is not repeated here.
  const notes = (d.plan.warnings || []).filter(w => !/OC No\. is given automatically/.test(w))
    .map(w => d.orders.length === 1 ? w.replace(/^Order \d+(: |, )/, '') : w).map(w => w.charAt(0).toUpperCase() + w.slice(1));
  return groups({ who, warnings: notes,
    newMasters: ex.new_masters || [], notSaved: ex.not_saved || [], conversions: ex.conversions || [], exclusions: ex.exclusions || [],
    moved: ex.layout_changes || [], referenceOnly: [] });
}

export function receiptGroups(result) {
  const ns = result.not_saved || [];
  return groups({ who: () => '', warnings: [], newMasters: [], notSaved: ns, conversions: result.conversions || [],
    exclusions: result.exclusions || [], moved: [],
    referenceOnly: (result.reference_only || []).filter(r => !ns.some(x => x.column === r.table)) });
}

function groups({ who, warnings, newMasters, notSaved, conversions, exclusions, moved, referenceOnly }) {
  const out = [];
  if (warnings.length) out.push({ title: 'To check', rows: warnings.map(w => ({ label: w, chip: 'Check', tone: 'check', plain: true })) });
  if (conversions.length) out.push({ title: 'Converted', rows: conversions.map(x => ({ label: `${who(x.order)}Quantity unit`, value: x.text, chip: 'Converted', tone: 'converted' })) });
  if (moved.length) out.push({ title: 'Layout changed', rows: moved.map(x => ({ label: x, chip: 'Check', tone: 'moved' })) });
  if (newMasters.length) out.push({ title: 'New in Oracle', rows: newMasters.map(x => ({ label: x.business, value: x.label, chip: 'New', tone: 'new', tip: `Not found in Oracle, so it will be added (${x.table})` })) });
  const ns = [...notSaved.map(x => ({ label: who(x.order) + x.label, value: quoted(x), chip: x.kept === 'unverified' ? 'Not set up yet' : 'No Oracle field', tone: 'skipped', tip: x.reason[0].toUpperCase() + x.reason.slice(1) })),
    ...referenceOnly.map(r => ({ label: r.business, value: plural(r.rows, 'row'), chip: 'Not set up yet', tone: 'skipped', tip: r.reason }))];
  if (ns.length) out.push({ title: 'Not saved to Oracle', note: 'kept with this import in POPPYS', rows: ns });
  if (exclusions.length) out.push({ title: 'Not read from the file', rows: exclusions.map(x => { const [a, b] = split(x); return { label: a, value: b, chip: 'Skipped', tone: 'skipped' }; }) });
  return out;
}

export function reviewList(list, { title = 'Review', ack = null } = {}) {
  if (!list.length) return '';
  const count = list.reduce((n, g) => n + g.rows.length, 0);
  return `<section class="card review-list" aria-labelledby="rl-title">
    <div class="card-head"><div><h2 id="rl-title">${esc(title)}</h2></div><div class="rl-counts">${list.map(g => `<span class="pill">${esc(g.title)} · ${g.rows.length}</span>`).join('')}</div></div>
    <table class="rl"><caption class="sr-only">${esc(title)}: ${count} items</caption>
      ${list.map(g => `<tbody><tr class="rl-group"><th colspan="3" scope="colgroup">${esc(g.title)}${g.note ? ` <span class="muted">· ${esc(g.note)}</span>` : ''}</th></tr>
        ${g.rows.map(r => `<tr><th scope="row" ${r.value ? '' : 'colspan="2"'} ${r.plain ? 'class="rl-note"' : ''}>${esc(r.label)}</th>${r.value ? `<td>${esc(r.value)}</td>` : ''}
          <td class="rl-chip"><span class="pill ${TONES[r.tone] || ''}" ${r.tip ? `title="${esc(r.tip)}"` : ''}>${esc(r.chip)}</span></td></tr>`).join('')}</tbody>`).join('')}
    </table>
    ${ack ? `<label class="check rl-ack"><input type="checkbox" id="ex-seen" ${ack.checked ? 'checked' : ''}> I’ve reviewed these items</label>` : ''}
  </section>`;
}

// The popup's notes in a few short lines a non-technical person reads at a glance. The full sentence of each point is
// kept in its tooltip. Notes about fabric, yarn and trims are left out while those rows are not saved at all.
function plainNote(text, pieces) {
  let m;
  if (/order quantities will be imported as a DEMO SAMPLE order/i.test(text)) return { icon: '✓', text: `Pieces: taken from the buyer order${pieces ? ` (${pieces} pcs)` : ''}` };
  if ((m = text.match(/“(.+?)” is the existing (?:\w+ )*?(\w+) “(.+?)”/i))) return { icon: '↔', text: `“${m[1]}” = existing ${m[2].toLowerCase()} “${m[3]}”` };
  return { icon: '!', text: text.length > 110 ? text.slice(0, 107) + '…' : text };
}

export function notesHtml(list, { pieces = '' } = {}) {
  const by = Object.fromEntries(list.map(g => [g.title, g.rows]));
  const notSaved = by['Not saved to Oracle'] || [];
  const bomOff = notSaved.some(r => /fabric|trim|accessor|yarn/i.test(r.label));
  const check = [...(by['To check'] || []), ...(by['Converted'] || []), ...(by['Layout changed'] || [])]
    .filter(r => !(bomOff && /accessor|fabric|yarn|trim/i.test(r.label)));
  const unread = by['Not read from the file'] || [];
  const words = [...new Set(unread.flatMap(r => ((r.value.match(/under (.+?) at row/) || [])[1] || '').split(', ')
    .map(w => w.trim()).filter(w => w && w !== '…' && !/qty|quantity|req|yy|rejection/i.test(w))))];
  const lines = check.map(r => { const n = plainNote(r.value ? `${r.label}: ${r.value}` : r.label, pieces); return { ...n, tip: r.label }; });
  if (notSaved.length) {
    lines.push({ icon: '✕', text: `Not saved in Oracle: ${notSaved.map(r => r.label.replace(/ (rows|split rows)$/i, '').replace(/^Trims \/ accessory$/i, 'Trims')).join(', ')}`,
                 tip: notSaved.map(r => `${r.label}: ${r.value} — ${r.tip || r.chip}`).join('\n') });
  }
  if (unread.length) {
    lines.push({ icon: '⋯', text: `Extra columns in the Excel not used${words.length ? `: ${words.slice(0, 5).join(', ')}` : ''}`,
                 tip: unread.map(r => `${r.label}: ${r.value}`).join('\n') });
  }
  if (!lines.length) return '';
  return `<section class="pv pv-notes"><h3>Please note</h3><ul class="pv-simple">${lines.map(l =>
    `<li title="${esc(l.tip || l.text)}"><span class="pv-ic" aria-hidden="true">${l.icon}</span>${esc(l.text)}</li>`).join('')}</ul></section>`;
}
