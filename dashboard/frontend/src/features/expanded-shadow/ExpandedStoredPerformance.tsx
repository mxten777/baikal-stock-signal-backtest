import { useEffect, useState } from "react";
import { dashboardApi } from "../../api/dashboardApi";
import { ExpandedStoredPerformance as StoredPerformance } from "../../types/expandedShadow";

const percent = (value: number | null) => value === null ? "—" : `${value.toFixed(2)}%`;

export function ExpandedStoredPerformance() {
  const [data, setData] = useState<StoredPerformance | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    dashboardApi.getExpandedStoredPerformance()
      .then((result) => { if (active) setData(result); })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Stored performance API unavailable");
      });
    return () => { active = false; };
  }, []);
  return <section className="panel expanded-section" aria-label="Expanded 검증 성과">
    <h3>Expanded 저장 성과 · 5D / 10D / 20D</h3>
    <p>완료 원장만 조회 · PARTIAL 미포함 · 충돌 신호는 모든 기간 집계 보류. 미도래·미확정은 0이 아닙니다.</p>
    <p>저장값의 신호 연결·유한 수치·초과수익 산술 검증 기준입니다. 최신 시세 재검증·실현손익이 아니며 비용 미반영, 상승 비율은 수익률 &gt; 0 기준입니다.</p>
    {error ? <p role="alert">{error}</p> : !data ? <p>저장 성과 확인 중</p> : <>
      <p>조회 기준일 {data.cutoff_date} · 기존 후보 {data.total}건 · 달력은 기존 KRX 규칙 기반</p>
      <div className="expanded-table-wrap"><table className="expanded-table">
        <thead><tr><th>기간</th><th>평가 건수</th><th>평균 수익률</th><th>상승 비율</th><th>미도래</th><th>미확정/누락</th><th>충돌 보류</th><th>Benchmark 확인 건수</th></tr></thead>
        <tbody>{data.horizons.map((row) => <tr key={row.horizon}>
          <td>{row.horizon}</td><td>{row.evaluated}</td><td>{percent(row.average_return)}</td><td>{percent(row.win_rate)}</td>
          <td>{row.not_due}</td><td>{row.unconfirmed}</td><td>{row.conflict_held}</td><td>{row.benchmark_verified}</td>
        </tr>)}</tbody>
      </table></div>
      <h4>수익률 충돌 · 공식 근거 확인 전 보류</h4>
      {data.conflicts.length === 0 ? <p>등록된 충돌 신호 없음</p> : <ul>{data.conflicts.map((item) =>
        <li key={`${item.ticker}-${item.signal_date}`}>{item.name} ({item.ticker}) · 신호일 {item.signal_date} · {item.conflict_horizon}D 충돌 · 원본 수치 보존</li>
      )}</ul>}
    </>}
  </section>;
}
