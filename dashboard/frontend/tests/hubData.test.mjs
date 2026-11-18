import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = readFileSync(new URL("../src/hubData.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 } });
const { validateData, normalizeUrl, stockLinks } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const fixture = () => ({
  version: 1,
  sites: [
    { id: "s1", name: "Research", url: "https://example.com/", category: "공시", favorite: true },
    { id: "s2", name: "Charts", url: "http://example.org/", category: "차트", favorite: false },
  ],
  stocks: [{ id: "t1", name: "삼성전자", code: "005930", category: "반도체", favorite: true, memo: "첫 줄\n두 번째 줄" }],
});

test("backup round trip preserves site order, categories, favorites, ticker zeros and memo", () => {
  const original = fixture();
  original.sites.reverse();
  assert.deepEqual(validateData(JSON.parse(JSON.stringify(original))), original);
});

test("only absolute credential-free HTTP(S) addresses are accepted", () => {
  for (const url of ["javascript:alert(1)", "data:text/html,test", "file:///C:/test", "ftp://example.com", "//example.com", "https://", "https://user:pass@example.com", "https://exa mple.com", "https:\\\\example.com", "https://example.com/\npath"]) {
    assert.throws(() => normalizeUrl(url), undefined, url);
    const data = fixture();
    data.sites[0].url = url;
    assert.throws(() => validateData(data), undefined, url);
  }
  assert.equal(normalizeUrl("HTTPS://EXAMPLE.COM"), "https://example.com/");
  assert.throws(() => normalizeUrl(`https://example.com/${"가".repeat(300)}`), /2,048/);
});

test("invalid schemas and duplicate identifiers/codes fail without changing the source", () => {
  for (const mutate of [
    (data) => { data.version = 2; },
    (data) => { data.sites = {}; },
    (data) => { data.stocks = null; },
    (data) => { data.sites[0].favorite = "yes"; },
    (data) => { data.sites[0].name = " "; },
    (data) => { data.sites[0].category = ""; },
    (data) => { data.sites[0].id = data.stocks[0].id; },
    (data) => { data.stocks.push({ ...data.stocks[0], id: "t2" }); },
    (data) => { data.stocks[0].code = "../bad"; },
    (data) => { delete data.stocks[0].memo; },
    (data) => { data.stocks[0].memo = "a".repeat(20001); },
  ]) {
    const data = fixture();
    mutate(data);
    const before = JSON.stringify(data);
    assert.throws(() => validateData(data));
    assert.equal(JSON.stringify(data), before);
  }
  for (const value of [null, [], {}, "text"]) assert.throws(() => validateData(value));
  const data = fixture();
  data.stocks = [{ ...data.stocks[0], code: "aapl" }, { ...data.stocks[0], id: "t2", code: "AAPL" }];
  assert.throws(() => validateData(data));
});

test("aggregate size limits guarantee that accepted data can be backed up and restored", () => {
  const data = fixture();
  data.stocks = Array.from({ length: 40 }, (_, i) => ({
    ...data.stocks[0], id: `t${i}`, code: `T${i}`, memo: "한".repeat(20000),
  }));
  assert.throws(() => validateData(data), /2MB/);
  data.stocks = [];
  data.sites = Array.from({ length: 2001 }, (_, i) => ({ ...data.sites[0], id: `s${i}` }));
  assert.throws(() => validateData(data), /2,000/);
});

test("domestic and international stock links use the correct encoded symbol", () => {
  const domestic = stockLinks(fixture().stocks[0]);
  assert.equal(domestic[0].url, "https://finance.naver.com/item/main.naver?code=005930");
  assert.equal(domestic[1].url, "https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?gicode=A005930");
  const overseas = stockLinks({ ...fixture().stocks[0], code: "BRK-B", name: "Berkshire" });
  assert.equal(overseas[0].url, "https://finance.yahoo.com/quote/BRK-B/");
  assert.ok(overseas[1].url.includes("BRK-B%20Berkshire"));
});
