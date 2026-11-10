import { useEffect, useState } from "react";
import { dashboardApi } from "../../api/dashboardApi";
import { ExpandedIsolatedReady as IsolatedReadyResponse } from "../../types/expandedShadow";

export function ExpandedIsolatedReady() {
  const [data, setData] = useState<IsolatedReadyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    dashboardApi.getExpandedIsolatedReady()
      .then((payload) => {
        if (payload.status !== "READY" || payload.scope !== "ISOLATED" || payload.read_only !== true
          || payload.operational_published !== false || payload.promoted !== false) {
          throw new Error("격리 READY 조회 응답의 상태/범위가 일치하지 않습니다.");
        }
        if (active) setData(payload);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "격리 READY API 조회 실패");
      });
    return () => { active = false; };
  }, []);

  return <section className="panel expanded-section" aria-label="격리 READY · 운영 미게시">
    <h3>{data ? `${data.source_date.slice(5).replace("-", "/")} 격리 READY — 운영 미게시` : "격리 READY — 운영 미게시"}</h3>
    <p>별도 읽기 전용 결과 · 운영 READY 승격 없음 · 승인 경계·원장·성과에 미반영</p>
    {error ? <p role="alert">격리 READY 조회/검증 실패: {error}. 기존 PARTIAL·운영 READY는 유지합니다.</p>
      : !data ? <p>격리 READY 확인 중</p>
      : <>
        <p>분석일 {data.source_date} · 처리 {data.processed}/{data.universe} · 신호 {data.signals} · 후보 {data.candidates}</p>
        <p>공식 거래정지 제외 {data.excluded.length}종목: {data.excluded.map((item) => `${item.name} (${item.ticker})`).join(", ") || "없음"}</p>
        <p>{data.active.map((item) => `${item.name} (${item.ticker}) ${item.status}`).join(", ")} · UNKNOWN {data.unknown}건</p>
        <div className="expanded-table-wrap">
          <table className="expanded-table">
            <thead><tr><th>종목</th><th>코드</th><th>신호</th><th>점수</th><th>분류</th></tr></thead>
            <tbody>{data.records.map((record) => <tr key={record.ticker}>
              <td>{record.name}</td><td>{record.ticker}</td><td>{record.signal_type}</td>
              <td>{record.signal_score}</td><td>{record.decision}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </>}
  </section>;
}
