import { useState, type FormEvent } from "react";
import { initialData, MAX_BACKUP_BYTES, normalizeUrl, STORAGE_KEY, stockLinks, validateData, validCode, type HubData, type Site, type Stock } from "./hubData";

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return { data: raw === null ? initialData : validateData(JSON.parse(raw)), raw, error: "" };
  } catch (error) {
    return { data: null, raw: null, error: `저장 데이터를 읽지 못했습니다. 기존 저장소는 변경하지 않았습니다. 유효한 백업을 복원하거나 브라우저 저장 권한을 확인하세요. ${error instanceof Error ? error.message : String(error)}` };
  }
}

const blankSite = { name: "", url: "", category: "증권" };
const blankStock = { name: "", code: "", category: "관심종목", memo: "" };

export function InvestmentHub() {
  const [stored, setStored] = useState(load);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [siteForm, setSiteForm] = useState(blankSite);
  const [stockForm, setStockForm] = useState(blankStock);
  const [editingSite, setEditingSite] = useState<string | null>(null);
  const [editingStock, setEditingStock] = useState<string | null>(null);
  const [pending, setPending] = useState<HubData | null>(null);
  const [reading, setReading] = useState(false);
  const data = stored.data;
  const categories = [...new Set([...(data?.sites ?? []), ...(data?.stocks ?? [])].map((item) => item.category))].sort();
  const search = query.trim().toLocaleLowerCase();
  const matches = (item: Site | Stock) => (!category || item.category === category)
    && (!favoritesOnly || item.favorite)
    && [item.name, item.category, ...("url" in item ? [item.url] : [item.code, item.memo])]
      .some((value) => value.toLocaleLowerCase().includes(search));
  const sites = data?.sites.filter(matches) ?? [];
  const stocks = data?.stocks.filter(matches) ?? [];

  function fail(cause: unknown) {
    setMessage("");
    setError(cause instanceof Error ? cause.message : String(cause));
  }

  function save(next: HubData, notice: string, restoring = false): boolean {
    try {
      const validated = validateData(next);
      if (!restoring && localStorage.getItem(STORAGE_KEY) !== stored.raw) {
        throw new Error("다른 탭에서 저장 데이터가 변경되었습니다. 새로고침 후 다시 수정하세요.");
      }
      const raw = JSON.stringify(validated);
      localStorage.setItem(STORAGE_KEY, raw);
      setStored({ data: validated, raw, error: "" });
      if (category && ![...validated.sites, ...validated.stocks].some((item) => item.category === category)) setCategory("");
      setMessage(notice);
      setError("");
      return true;
    } catch (cause) {
      fail(`저장하지 못했습니다. 기존 데이터는 유지됩니다. ${cause instanceof Error ? cause.message : String(cause)}`);
      return false;
    }
  }

  function submitSite(event: FormEvent) {
    event.preventDefault();
    if (!data) return;
    try {
      const site: Site = {
        id: editingSite ?? crypto.randomUUID(), name: siteForm.name.trim(), url: normalizeUrl(siteForm.url.trim()),
        category: siteForm.category.trim(), favorite: data.sites.find((item) => item.id === editingSite)?.favorite ?? false,
      };
      if (save({ ...data, sites: editingSite ? data.sites.map((item) => item.id === editingSite ? site : item) : [...data.sites, site] }, "사이트를 저장했습니다.")) {
        setEditingSite(null);
        setSiteForm(blankSite);
      }
    } catch (cause) { fail(cause); }
  }

  function submitStock(event: FormEvent) {
    event.preventDefault();
    if (!data) return;
    const code = stockForm.code.trim().toUpperCase();
    if (!validCode(code)) { fail("종목코드는 영문·숫자·점·하이픈·^를 사용한 최대 20자입니다."); return; }
    const stock: Stock = {
      ...stockForm, id: editingStock ?? crypto.randomUUID(), name: stockForm.name.trim(), code,
      category: stockForm.category.trim(), favorite: data.stocks.find((item) => item.id === editingStock)?.favorite ?? false,
    };
    if (save({ ...data, stocks: editingStock ? data.stocks.map((item) => item.id === editingStock ? stock : item) : [...data.stocks, stock] }, "종목과 메모를 저장했습니다.")) {
      setEditingStock(null);
      setStockForm(blankStock);
    }
  }

  function moveSite(id: string, direction: number) {
    if (!data) return;
    const items = [...data.sites];
    const index = items.findIndex((item) => item.id === id);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= items.length) return;
    [items[index], items[target]] = [items[target], items[index]];
    save({ ...data, sites: items }, "사이트 순서를 저장했습니다.");
  }

  function backup() {
    if (!data) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `baikal-hub-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setMessage("JSON 백업 다운로드를 요청했습니다. 다운로드 폴더를 확인하세요.");
    setError("");
  }

  async function readBackup(file: File | undefined) {
    setPending(null);
    setMessage("");
    setError("");
    if (!file) return;
    setReading(true);
    try {
      if (file.size > MAX_BACKUP_BYTES) throw new Error("백업 파일은 최대 2MB입니다.");
      setPending(validateData(JSON.parse(await file.text())));
    } catch (cause) {
      fail(`복원 파일 검증 실패. 기존 데이터는 변경하지 않았습니다. ${cause instanceof Error ? cause.message : String(cause)}`);
    } finally { setReading(false); }
  }

  function restore() {
    if (pending && save(pending, "백업을 복원했습니다.", true)) {
      setPending(null);
      setEditingSite(null); setSiteForm(blankSite);
      setEditingStock(null); setStockForm(blankStock);
      setCategory(""); setQuery(""); setFavoritesOnly(false);
    }
  }

  return <main className="main-content hub">
    <header className="hub-header">
      <div><p className="eyebrow">BAIKAL · INVESTMENT WORKSPACE</p><h1>BAIKAL 투자정보 허브</h1>
        <p className="muted">필요한 사이트, 관심 종목, 나만의 투자 메모를 한곳에.</p></div>
      <span className="local-badge">브라우저 로컬 저장 · 서버/API 불필요</span>
    </header>
    <section className="panel toolbar" aria-label="통합 검색 및 필터">
      <label className="search-field">통합 검색<input type="search" placeholder="사이트 · 종목명 · 코드 · 메모 검색" value={query} onChange={(e) => setQuery(e.target.value)} /></label>
      <label>카테고리<select value={category} onChange={(e) => setCategory(e.target.value)}>
        <option value="">전체 카테고리</option>{categories.map((name) => <option key={name}>{name}</option>)}
      </select></label>
      <label className="check-label"><input type="checkbox" checked={favoritesOnly} onChange={(e) => setFavoritesOnly(e.target.checked)} />즐겨찾기만</label>
      <button type="button" onClick={() => { setQuery(""); setCategory(""); setFavoritesOnly(false); }}>필터 초기화</button>
    </section>
    {(error || stored.error) && <p role="alert" className="notice error">{error || stored.error}</p>}
    {message && <p role="status" className="notice success">{message}</p>}
    <datalist id="hub-categories">{categories.map((name) => <option key={name} value={name} />)}</datalist>
    {data && <>
      <div className="workspace-grid">
        <section className="panel" aria-labelledby="sites-heading">
          <div className="section-title"><h2 id="sites-heading">투자정보 사이트</h2><span className="count">{sites.length} / {data.sites.length}</span></div>
          <p className="muted">카테고리별로 모으고, 위·아래 버튼으로 전체 순서를 바꾸세요.</p>
          <form onSubmit={submitSite} className="editor" aria-label="사이트 편집">
            <h3>{editingSite ? "사이트 수정" : "사이트 등록"}</h3>
            <label>사이트 이름<input required maxLength={100} value={siteForm.name} onChange={(e) => setSiteForm({ ...siteForm, name: e.target.value })} /></label>
            <label>사이트 주소<input required maxLength={2048} placeholder="https://example.com" value={siteForm.url} onChange={(e) => setSiteForm({ ...siteForm, url: e.target.value })} /></label>
            <label>사이트 카테고리<input required maxLength={50} list="hub-categories" value={siteForm.category} onChange={(e) => setSiteForm({ ...siteForm, category: e.target.value })} /></label>
            <div className="actions"><button className="primary" type="submit">{editingSite ? "사이트 수정 저장" : "사이트 추가"}</button>
              {editingSite && <button type="button" onClick={() => { setEditingSite(null); setSiteForm(blankSite); }}>사이트 수정 취소</button>}</div>
          </form>
          <div className="card-list">{sites.map((site) => <article className="item-card" key={site.id} aria-label={`사이트 ${site.name}`}>
            <div className="card-heading"><a className="item-title" href={site.url} target="_blank" rel="noopener noreferrer">{site.name} ↗</a>
              <button type="button" className="favorite" aria-label={`${site.name} 즐겨찾기`} aria-pressed={site.favorite}
                onClick={() => save({ ...data, sites: data.sites.map((item) => item.id === site.id ? { ...item, favorite: !item.favorite } : item) }, "즐겨찾기를 저장했습니다.")}>{site.favorite ? "★" : "☆"}</button></div>
            <span className="category-tag">{site.category}</span><p className="site-url muted">{site.url}</p>
            <div className="actions">
              <button type="button" onClick={() => { setEditingSite(site.id); setSiteForm({ name: site.name, url: site.url, category: site.category }); }}>수정</button>
              <button type="button" disabled={data.sites[0]?.id === site.id} aria-label={`${site.name} 위로`} onClick={() => moveSite(site.id, -1)}>↑ 위로</button>
              <button type="button" disabled={data.sites[data.sites.length - 1]?.id === site.id} aria-label={`${site.name} 아래로`} onClick={() => moveSite(site.id, 1)}>↓ 아래로</button>
              <button type="button" className="danger" onClick={() => {
                if (window.confirm(`"${site.name}" 사이트를 삭제할까요?`) && save({ ...data, sites: data.sites.filter((item) => item.id !== site.id) }, "사이트를 삭제했습니다.") && editingSite === site.id) { setEditingSite(null); setSiteForm(blankSite); }
              }}>삭제</button>
            </div>
          </article>)}</div>
          {!sites.length && <p className="empty">표시할 사이트가 없습니다. 등록하거나 검색·필터를 확인하세요.</p>}
        </section>
        <section className="panel" aria-labelledby="stocks-heading">
          <div className="section-title"><h2 id="stocks-heading">관심 종목 · 투자 메모</h2><span className="count">{stocks.length} / {data.stocks.length}</span></div>
          <p className="muted">국내 6자리 코드 또는 해외 티커를 등록하고 전문 사이트에서 확인하세요.</p>
          <form onSubmit={submitStock} className="editor" aria-label="종목 편집">
            <h3>{editingStock ? "종목 · 메모 수정" : "관심 종목 등록"}</h3>
            <div className="form-row">
              <label>종목명<input required maxLength={100} value={stockForm.name} onChange={(e) => setStockForm({ ...stockForm, name: e.target.value })} /></label>
              <label>종목코드<input required maxLength={20} placeholder="005930 / AAPL" value={stockForm.code} onChange={(e) => setStockForm({ ...stockForm, code: e.target.value })} /></label>
            </div>
            <label>종목 카테고리<input required maxLength={50} list="hub-categories" value={stockForm.category} onChange={(e) => setStockForm({ ...stockForm, category: e.target.value })} /></label>
            <label>투자 메모<textarea maxLength={20000} rows={4} placeholder="확인할 자료, 투자 근거, 주의할 점 등을 자유롭게 기록하세요." value={stockForm.memo} onChange={(e) => setStockForm({ ...stockForm, memo: e.target.value })} /></label>
            <div className="actions"><button className="primary" type="submit">{editingStock ? "종목·메모 수정 저장" : "관심 종목 추가"}</button>
              {editingStock && <button type="button" onClick={() => { setEditingStock(null); setStockForm(blankStock); }}>종목 수정 취소</button>}</div>
          </form>
          <div className="card-list">{stocks.map((stock) => <article className="item-card" key={stock.id} aria-label={`종목 ${stock.name}`}>
            <div className="card-heading"><h3>{stock.name} <span className="muted">{stock.code}</span></h3>
              <button type="button" className="favorite" aria-label={`${stock.name} 즐겨찾기`} aria-pressed={stock.favorite}
                onClick={() => save({ ...data, stocks: data.stocks.map((item) => item.id === stock.id ? { ...item, favorite: !item.favorite } : item) }, "즐겨찾기를 저장했습니다.")}>{stock.favorite ? "★" : "☆"}</button></div>
            <span className="category-tag">{stock.category}</span>
            <nav className="external-links" aria-label={`${stock.name} 전문 사이트`}>{stockLinks(stock).map((link) => <a key={link.name} href={link.url} target="_blank" rel="noopener noreferrer">{link.name} ↗</a>)}</nav>
            <p className="memo">{stock.memo || "아직 투자 메모가 없습니다."}</p>
            <div className="actions"><button type="button" onClick={() => { setEditingStock(stock.id); setStockForm({ name: stock.name, code: stock.code, category: stock.category, memo: stock.memo }); }}>종목·메모 수정</button>
              <button type="button" className="danger" onClick={() => {
                if (window.confirm(`"${stock.name}" 종목과 메모를 삭제할까요?`) && save({ ...data, stocks: data.stocks.filter((item) => item.id !== stock.id) }, "종목과 메모를 삭제했습니다.") && editingStock === stock.id) { setEditingStock(null); setStockForm(blankStock); }
              }}>삭제</button></div>
          </article>)}</div>
          {!stocks.length && <p className="empty">표시할 관심 종목이 없습니다. 등록하거나 검색·필터를 확인하세요.</p>}
        </section>
      </div>
    </>}
    <section className="panel backup-panel" aria-labelledby="backup-heading">
      <div><h2 id="backup-heading">JSON 백업 · 복원</h2>
        <p className="muted">이 브라우저·주소에만 저장됩니다. 브라우저 데이터 삭제 전 백업하세요. 다른 기기로 자동 동기화되지 않습니다.</p></div>
      <div className="actions"><button type="button" disabled={!data} onClick={backup}>JSON 백업 다운로드</button>
        <label className="file-label">JSON 복원 파일 선택<input type="file" accept=".json,application/json" disabled={reading} onChange={(e) => { void readBackup(e.target.files?.[0]); e.target.value = ""; }} /></label></div>
      {reading && <p role="status">백업 파일 검증 중…</p>}
      {pending && <div className="restore-preview"><p>검증 완료: 사이트 {pending.sites.length}개 · 종목 {pending.stocks.length}개. 복원하면 현재 사이트·종목·메모를 모두 대체합니다. 먼저 현재 데이터를 백업하세요.</p>
        <div className="actions"><button type="button" className="primary" onClick={restore}>현재 데이터를 백업 내용으로 교체</button><button type="button" onClick={() => setPending(null)}>복원 취소</button></div></div>}
    </section>
    <footer className="muted">외부 링크는 새 탭에서 열립니다. 자동 수집·AI 분석·추천·매매 신호는 제공하지 않습니다. 메모에 민감정보를 입력하지 마세요.</footer>
  </main>;
}
