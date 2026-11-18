import { useEffect, useState } from "react";

type Market = { date: string; close: number | null; volume: number | null };
type Flow = { date: string; foreign_net_buy: number | null; institution_net_buy: number | null };
type Finance = { date: string; period: string; revenue: number | null; operating_income: number | null; net_income: number | null };
type Company = { ticker: string; name: string; market: Market | null; flow: Flow | null; finance: Finance | null };
type Disclosure = { rcept_no: string; name: string; rcept_dt: string; report_nm: string; original_url: string };
type Overview = {
  companies: Company[];
  dart: { items: Disclosure[]; collected_at: string | null; window_end: string | null } | null;
};

const number = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 });
const won = (value: number | null) => value === null ? "미제공" : `${number.format(value)}원`;
const shares = (value: number | null) => value === null ? "미제공" : `${number.format(value)}주`;
const local = ["127.0.0.1", "localhost", "[::1]"].includes(window.location.hostname);

export function InvestmentOverview() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState(false);
  const [ticker, setTicker] = useState("005930");
  useEffect(() => {
    const request = new AbortController();
    fetch("/api/dashboard/overview?view=investment", { signal: request.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const overview: Overview = await response.json();
        if (!Array.isArray(overview.companies)) throw new Error("Investment overview unavailable");
        setData(overview);
      })
      .catch(() => { if (!request.signal.aborted) setError(true); });
    return () => request.abort();
  }, []);

  const company = data?.companies.find((item) => item.ticker === ticker);
  return <main className="main-content" style={{ maxWidth: 960, margin: "0 auto", padding: "24px 16px", display: "grid", gap: 20 }}>
    <header>
      <h1>BAIKAL Stock Signal</h1>
      <p>투자정보 · 저장 자료 읽기 전용</p>
    </header>
    {error ? <p role="alert">저장 자료를 불러오지 못했습니다. Dashboard 서버를 확인한 뒤 새로고침하세요.</p>
      : !data ? <p role="status">저장 자료 조회 중…</p> : <>
        <section>
          <h2>DART 주요 공시</h2>
          {local && <p><a href="/api/disclosure-analysis"><u>저장된 공시 카드 및 접수일 보기</u></a></p>}
          {data.dart ? <>
            <p>마지막 확인된 수집: {data.dart.collected_at
              ? new Date(data.dart.collected_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" }) + " (KST)"
              : "미확인"}{data.dart.window_end && ` · 수집 대상 종료일 ${data.dart.window_end}`}</p>
            <p>수집 이후에 접수된 공시는 포함되지 않을 수 있습니다.</p>
            {data.dart.items.length ? <ul style={{ paddingLeft: 20 }}>
              {data.dart.items.map((item) => <li key={item.rcept_no}>
                <a href={item.original_url} target="_blank" rel="noopener noreferrer">
                  <u>{item.rcept_dt.replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3")} · {item.name} · {item.report_nm}</u>
                </a>
              </li>)}
            </ul> : <p>저장된 주요 공시가 없습니다.</p>}
          </> : <p>공시 저장소를 확인할 수 없습니다.</p>}
        </section>
        <section>
          <h2>종목별 저장 자료</h2>
          <p>시세·수급은 실시간 자료가 아니며 자동 갱신하지 않습니다. 재무는 저장된 보고기간과 공시일을 표시합니다.</p>
          <label>종목 <select value={ticker} onChange={(event) => setTicker(event.target.value)}>
            {data.companies.map((item) => <option key={item.ticker} value={item.ticker}>{item.name} ({item.ticker})</option>)}
          </select></label>
          {company && <div style={{ display: "grid", gap: 12, marginTop: 12 }}>
            <div>
              <h3>주가·거래량</h3>
              {company.market ? <p>{company.market.date} 기준 · 종가 {won(company.market.close)} · 거래량 {shares(company.market.volume)}</p>
                : <p>저장 자료 없음</p>}
            </div>
            <div>
              <h3>외국인·기관 수급</h3>
              {company.flow ? <p>{company.flow.date} 기준 · 외국인 순매수 {shares(company.flow.foreign_net_buy)} · 기관 순매수 {shares(company.flow.institution_net_buy)}</p>
                : <p>저장 자료 없음</p>}
            </div>
            <div>
              <h3>기업 재무</h3>
              {company.finance ? <p>{company.finance.period} · 공시일 {company.finance.date} · 매출 {won(company.finance.revenue)} · 영업이익 {won(company.finance.operating_income)} · 순이익 {won(company.finance.net_income)}</p>
                : <p>저장 자료 없음</p>}
            </div>
          </div>}
        </section>
      </>}
  </main>;
}
