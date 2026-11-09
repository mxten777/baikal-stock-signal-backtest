import { useEffect, useState } from "react";
import { dashboardApi } from "../../api/dashboardApi";
import { ExpandedPartialResponse } from "../../types/expandedShadow";
import "./ExpandedSignalBoard.css";

export function ExpandedPartialBoard() {
  const [data, setData] = useState<ExpandedPartialResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    dashboardApi.getExpandedPartialBoard()
      .then((response) => { if (active) setData(response); })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "PARTIAL API unavailable");
      });
    return () => { active = false; };
  }, []);

  return <section className="expanded-page" aria-label="Expanded PARTIAL results">
    <header className="expanded-header">
      <div>
        <h2>Expanded PARTIAL · 격리 결과</h2>
        <p>전체 완료 아님 · Validation 미실행 · 운영 반영 없음</p>
      </div>
    </header>
    {error ? <p role="alert" className="expanded-error">{error}</p> : !data
      ? <p>Loading PARTIAL results...</p>
      : data.items.length === 0 ? <p>저장된 PARTIAL 결과 없음</p>
      : data.items.map((run) => <article key={run.run_id} className="expanded-panel">
        <h3>{run.source_date} · {run.status}</h3>
        <p>처리 {run.attempted}/{run.universe} · READY {run.ready} · 공식 정지 제외 {run.suspended} · UNKNOWN {run.unknown}</p>
        <p>신호 {run.signals} · 후보 {run.candidates} · 신호단계 제외 {run.signal_excluded}</p>
        <p>SUSPENDED: {run.excluded_tickers.join(", ") || "없음"} · UNKNOWN 보류: {run.unknown_tickers.join(", ") || "없음"}</p>
        <p>Validation {run.validation_status} · 미해결 {run.unresolved} · Run {run.run_id}</p>
        <div style={{ overflowX: "auto" }}>
          <table>
            <thead><tr><th>종목</th><th>코드</th><th>시장</th><th>분류</th><th>신호</th><th>점수</th></tr></thead>
            <tbody>{run.records.map((record) => <tr key={record.ticker}>
              <td>{record.name}</td><td>{record.ticker}</td><td>{record.market}</td>
              <td>{record.decision}</td><td>{record.signal_type}</td><td>{record.signal_score}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </article>)}
  </section>;
}
