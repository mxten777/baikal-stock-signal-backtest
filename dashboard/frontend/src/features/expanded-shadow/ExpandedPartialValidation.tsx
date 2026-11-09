import { useEffect, useState } from "react";
import { dashboardApi } from "../../api/dashboardApi";
import { ExpandedPartialValidationResponse, PartialEvaluationHorizon } from "../../types/expandedShadow";
import "./ExpandedSignalBoard.css";

const HORIZONS: PartialEvaluationHorizon[] = ["5D", "10D", "20D"];

export function ExpandedPartialValidation() {
  const [data, setData] = useState<ExpandedPartialValidationResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    dashboardApi.getExpandedPartialValidation()
      .then((response) => { if (active) setData(response); })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "PARTIAL Validation API unavailable");
      });
    return () => { active = false; };
  }, []);

  return <section className="expanded-page" aria-label="PARTIAL Validation results">
    <header className="expanded-header"><div>
      <h2>PARTIAL Validation · 별도 조회</h2>
      <p>전체 완료 아님 · 성과 검증 미실행 · 완료 성과와 합산하지 않음</p>
      <p>5D/10D/20D는 검증된 후속 데이터 없음: 미확정. 평가기간 도래 여부도 확정하지 않으며 수익률을 계산하거나 0으로 대체하지 않습니다.</p>
    </div></header>
    {error ? <p role="alert" className="expanded-error">{error}</p> : !data
      ? <p>Loading PARTIAL Validation...</p>
      : data.items.length === 0 ? <p>저장된 PARTIAL Validation 대상 없음</p>
      : data.items.map((run) => <article key={run.run_id} className="expanded-panel">
        <h3>{run.source_date} · {run.status}</h3>
        <p>처리 {run.attempted}/{run.universe} · 신호 {run.signals} · 후보 {run.candidates}</p>
        <p>SUSPENDED 제외 {run.suspended} · UNKNOWN 보류 {run.unknown} · Validation {run.validation_status}</p>
        <p>Run {run.run_id} · 운영 반영 없음</p>
        <div style={{ overflowX: "auto" }}><table>
          <thead><tr><th>종목</th><th>코드</th><th>신호 상태</th><th>후보 상태</th>
            {HORIZONS.map((horizon) => <th key={horizon}>{horizon} 성과</th>)}
          </tr></thead>
          <tbody>{run.records.map((record) => <tr key={record.ticker}>
            <td>{record.name}</td><td>{record.ticker}</td><td>{record.signal_type}</td>
            <td>{record.decision === "CANDIDATE" ? "후보 · CANDIDATE" : "신호단계 제외 · EXCLUDED"}</td>
            {HORIZONS.map((horizon) => <td key={horizon}>
              <span>{record.forward_evaluation[horizon].status} · 미확정</span><br />
              <small>Return — / Benchmark — / Excess —<br />검증된 후속 데이터 없음</small>
            </td>)}
          </tr>)}</tbody>
        </table></div>
      </article>)}
  </section>;
}
