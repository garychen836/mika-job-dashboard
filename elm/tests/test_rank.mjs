// Offline test of the page's scoring code (no browser, no network). Run: node elm/tests/test_rank.mjs
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
const html = readFileSync(new URL("../static/index.html", import.meta.url), "utf8");
const m = html.match(/\/\* RANK-BEGIN[\s\S]*?\/\* RANK-END \*\//);
assert(m, "rank block not found");
const Rank = new Function(m[0] + "\nreturn Rank;")();
const NOW = Date.parse("2026-11-04");
const R = (id, company, title, kind, extra = {}) => ({ id, company, title, kind, location: "Boston, MA", posted: "2026-10-01", ...extra });
const spons = { lca_recent: 30, lca_total: 90, last_fy: 2026 };
const roles = [
  R("a", "Acme", "Software Engineer Intern", "intern"),
  R("b", "Beta", "Data Analyst Intern", "intern"),
  R("c", "Gamma", "Junior Data Analyst", "entry"),
  R("d", "Delta", "Marketing Intern", "intern", { sponsor: spons }),
  R("e", "Beta", "Research Associate", "entry"),
  R("f", "Zed", "Sales Associate", "entry", { tier: "startup" }),
];
const order = (P, Lr) => Rank.sortRoles(roles, P, Lr, "rec", NOW).map(r => r.id);
const stats = { kind: "intern", majors: [{ id: "stats" }], chips: ["data"] };
assert.equal(order(stats)[0], "b", "data intern first for stats major wanting data internships");
assert(order(stats).indexOf("c") < order(stats).indexOf("e"), "entry data role beats unrelated entry role");
const why = Rank.score(roles[1], stats, null, NOW).why.map(w => w.k);
assert(why.includes("kind") && why.includes("chip"), why);
// 👎 drops a company; 👍 lifts one
let Lr = Rank.learn({ co: {}, w: {}, st: 0 }, roles[1], "down", 1, NOW);
assert(Rank.score(roles[1], stats, Lr, NOW).score < Rank.score(roles[1], stats, null, NOW).score - 1.4, "company weight lowers score");
assert(Rank.score(roles[4], stats, Lr, NOW).score < Rank.score(roles[4], stats, null, NOW).score, "same company drops too");
let Lu = Rank.learn(null, roles[3], "up", 1, NOW);
const sc = Rank.score(roles[3], {}, Lu, NOW); assert.equal(sc.liked, "Delta"); assert(sc.score > Rank.score(roles[3], {}, null, NOW).score);
Lu = Rank.learn(Lu, roles[3], "up", -1, NOW); assert.equal(Rank.score(roles[3], {}, Lu, NOW).liked, null, "undo removes it");
// sponsor boost only when the box is on
const off = Rank.score(roles[3], { kind: "intern", visa: false }, null, NOW), on = Rank.score(roles[3], { kind: "intern", visa: true }, null, NOW);
assert(on.score - off.score >= 2 && on.why.some(w => w.k === "h1b") && !off.why.some(w => w.k === "h1b"));
assert.equal(Rank.score(roles[0], { visa: true }, null, NOW).score, Rank.score(roles[0], { visa: false }, null, NOW).score, "no sponsor facts: no boost");
// keywords from the interview and free text; avoid demotes
assert.equal(Rank.sortRoles(roles, { keywords: ["marketing"] }, null, "rec", NOW)[0].id, "d");
assert.equal(Rank.sortRoles(roles, { text: "research, lab" }, null, "rec", NOW)[0].id, "e");
const av = Rank.score(roles[2], { chips: ["data"], avoid: ["junior"] }, null, NOW).score, nav = Rank.score(roles[2], { chips: ["data"] }, null, NOW).score;
assert(av < nav - 5, "avoid word demotes");
assert(Rank.score(roles[5], {}, { co: {}, w: {}, st: 1.5 }, NOW).why.some(w => w.k === "startup"), "startup preference shows");
assert(Rank.score(roles[0], { locations: ["boston"] }, null, NOW).why.some(w => w.k === "loc"));
// newest mode orders by date; decay and caps
assert.equal(Rank.sortRoles([R("o", "A", "x", "intern", { posted: "2026-01-01" }), R("n", "A", "x", "intern", { posted: "2026-10-30" })], {}, null, "new", NOW)[0].id, "n");
let big = { co: {}, w: {}, st: 0, t: NOW }; for (let i = 0; i < 500; i++) big = Rank.learn(big, R("i" + i, "Co" + i, "word" + i + " engineer", "intern"), "up", 1, NOW);
assert(Object.keys(big.co).length <= 150 && Object.keys(big.w).length <= 300, "size capped");
const old = Rank.decay({ co: { acme: 1 }, w: {}, st: 0, t: NOW - 120 * 864e5 }, NOW); assert(Math.abs(old.co.acme - 0.25) < 1e-9, "half-life 60 days");
assert(Rank.CHIPS.length === 24 && Rank.CHIPS.every(c => c.en && c.zh && c.kw.length) && Rank.MAJORS.length === 20);
// majors: up to 3, related chips are the union
const ids = P => Rank.relatedChips(P).map(o => o.chip).sort();
const u3 = ids({ majors: [{ id: "cs" }, { id: "hum" }, { id: "econ" }, { id: "law" }] });          // a 4th major is ignored
assert.deepEqual(Rank.majorList({ majors: ["cs", "hum", "econ", "law"] }).map(m => m.id), ["cs", "hum", "econ"]);
for (const c of [...Rank.MAJORS.find(m => m.id === "cs").chips, ...Rank.MAJORS.find(m => m.id === "hum").chips, ...Rank.MAJORS.find(m => m.id === "econ").chips]) assert(u3.includes(c), c);
assert(!u3.includes("law"), "the 4th major adds nothing");
const sw = R("s", "Co", "Software Engineer Intern", "intern"), po = R("p", "Co", "Policy Intern", "intern");
assert.equal(Rank.score(sw, { majors: [{ id: "hum" }] }, null, NOW).why.some(w => w.k === "major"), false, "history alone: software is not related");
assert(Rank.score(sw, { majors: [{ id: "hum" }, { id: "cs" }] }, null, NOW).why.some(w => w.k === "major" && w.id === "cs"), "double major: cs relates");
assert(Rank.score(po, { majors: [{ id: "hum" }, { id: "cs" }] }, null, NOW).why.some(w => w.k === "major" && w.id === "hum"));
// default exclusions: History + English major excludes technical families; any related chip of another major keeps its chip
const exHum = Rank.suggestExclude([{ id: "hum" }]);
for (const c of ["swe", "hwe", "ee", "mech"]) assert(exHum.includes(c), c);
assert(!exHum.includes("media") && !exHum.includes("policy"));
assert(!Rank.suggestExclude([{ id: "hum" }, { id: "cs" }]).includes("swe"), "cs + history: software stays");
assert.deepEqual(Rank.suggestExclude([{ id: "hum" }, { id: "other", text: "Astronomy" }]), [], "an unknown Other major: nothing guessed");
assert.deepEqual(Rank.suggestExclude([{ id: "other", text: "Astronomy" }]), []);
assert.deepEqual(Rank.suggestExclude([]), []);
assert.deepEqual(Rank.otherChips("Astronomy"), [], "no word matches a chip: no related chips");
assert(Rank.otherChips("Data analytics and marketing").includes("data") && Rank.otherChips("Data analytics and marketing").includes("marketing"));
assert(Rank.relatedChips({ majors: [{ id: "other", text: "Marketing analytics" }] }).some(o => o.chip === "marketing"), "typed Other major matched by its words");
// hiding: only when ALL matched chips are excluded; never silently (split returns the hidden list for the count line)
const rs = [R("h1", "A", "Software Engineer Intern", "intern"), R("h2", "A", "Electrical Engineer Intern", "intern"), R("h3", "A", "Policy Analyst Intern", "intern"),
  R("h4", "A", "Museum Collections Intern", "intern"), R("h5", "A", "Office Helper", "intern")];
const Px = { exclude: ["swe", "hwe", "ee", "mech"], chips: [], keywords: [], majors: [{ id: "hum" }] };
const sp = Rank.split(rs, Px, false);
assert.deepEqual(sp.hidden.map(r => r.id), ["h1", "h2"]);
assert.deepEqual(sp.show.map(r => r.id), ["h3", "h4", "h5"]);
assert.deepEqual(Rank.split(rs, Px, true).show.map(r => r.id), ["h1", "h2", "h3", "h4", "h5"], "show them restores everything");
assert.equal(Rank.isHidden(rs[1], { ...Px, exclude: ["ee"] }), false, "electrical engineer also matches software: not only excluded chips");
assert.equal(Rank.isHidden(rs[0], { ...Px, chips: ["swe"] }), false, "a chip the student picked is never hidden");
assert.equal(Rank.isHidden(rs[0], { ...Px, keywords: ["software"] }), false, "a keyword hit is never hidden");
assert.equal(Rank.isHidden(rs[0], { ...Px, exclude: [] }), false);
assert.equal(Rank.isHidden(rs[4], Px), false, "a title matching no family is shown");
// page strings: every new label in both languages, the count line mentions the number
const T = new Function(html.match(/const T = \{[\s\S]*?\n\};\s*\nlet lang/)[0].replace(/\nlet lang$/, "") + "\nObject.assign(T.en,{});" +
  [...html.matchAll(/Object\.assign\(T\.(en|zh),\{[\s\S]*?\n\}\);/g)].map(m => m[0]).join("\n") + "\nreturn T;")();
for (const k of Object.keys(T.en)) assert(k in T.zh, "missing zh label: " + k);
for (const k of Object.keys(T.zh)) assert(k in T.en, "missing en label: " + k);
assert(T.en.hideLine(3).includes("3 roles hidden because they don't match your majors and interests") && T.zh.hideLine(3).includes("3"));
// tracking CSV: formulas are escaped, round trip, import validation
const csvm = html.match(/\/\* CSV-BEGIN[\s\S]*?\/\* CSV-END \*\//); assert(csvm, "csv block not found");
const Csv = new Function(csvm[0] + "\nreturn Csv;")();
for (const [raw, out] of [["=SUM(A1)", "'=SUM(A1)"], ["+1", "'+1"], ["-2", "'-2"], ["@x", "'@x"], ["plain", "plain"], ["a,b", '"a,b"'], ['say "hi"', '"say ""hi"""'], ["x=1", "x=1"]]) assert.equal(Csv.esc(raw), out);
const ent = { id: "r1", company: "=cmd|' /C calc'!A0", title: "Data, \"Analyst\"\nIntern", location: "NYC", url: "https://example.com/j", stage: "applied", note: "@home", next: "2026-12-01", added: "2026-11-04" };
const csv = Csv.toCsv([ent]);
assert(!/(^|,)[=+\-@]/m.test(csv.split("\r\n")[1].replace(/"[^"]*(?:""[^"]*)*"/g, '""')), "no unescaped formula cell");
const back = Csv.fromCsv(csv)[0];
assert.equal(back.company, ent.company); assert.equal(back.title, ent.title); assert.equal(back.note, "@home"); assert.equal(back.stage, "applied"); assert.equal(back.next, "2026-12-01");
const bad = Csv.fromCsv("id,title,url,stage,next,note\r\nz1,T,javascript:alert(1),bogus,tomorrow,ok\r\n,notitle,,,,\r\n" + "x".repeat(10));
assert.equal(bad.length, 1); assert.equal(bad[0].url, ""); assert.equal(bad[0].stage, "saved"); assert.equal(bad[0].next, "");
assert.equal(Csv.fromCsv("").length, 0); assert.equal(Csv.fromCsv("id,title\r\n" + Array.from({ length: 600 }, (_, i) => `a${i},t`).join("\r\n")).length, 500, "import is capped");
console.log("elm rank: ALL OK");
