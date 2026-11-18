export type Site = { id: string; name: string; url: string; category: string; favorite: boolean };
export type Stock = { id: string; name: string; code: string; category: string; favorite: boolean; memo: string };
export type HubData = { version: 1; sites: Site[]; stocks: Stock[] };

export const STORAGE_KEY = "baikal.investment-hub.v1";
export const MAX_BACKUP_BYTES = 2 * 1024 * 1024;
export const initialData: HubData = {
  version: 1,
  sites: [
    { id: "naver", name: "네이버 증권", url: "https://finance.naver.com/", category: "증권", favorite: true },
    { id: "dart", name: "DART 전자공시", url: "https://dart.fss.or.kr/", category: "공시", favorite: true },
    { id: "kind", name: "KRX KIND", url: "https://kind.krx.co.kr/", category: "공시", favorite: false },
    { id: "tradingview", name: "TradingView", url: "https://kr.tradingview.com/", category: "차트", favorite: false },
  ],
  stocks: [],
};

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown, max: number, allowEmpty = false): value is string {
  return typeof value === "string" && value.length <= max && (allowEmpty || value.trim().length > 0);
}

export function normalizeUrl(value: string): string {
  if (!/^https?:\/\//i.test(value) || /[\s\\\u0000-\u001f\u007f]/.test(value)) {
    throw new Error("사이트 주소는 공백 없는 http:// 또는 https:// URL만 허용합니다.");
  }
  let url: URL;
  try { url = new URL(value); }
  catch { throw new Error("사이트 주소의 URL 형식을 확인하세요."); }
  if (!url.hostname || url.username || url.password) {
    throw new Error("사이트 주소의 호스트를 확인하세요. 인증정보가 포함된 URL은 허용하지 않습니다.");
  }
  if (url.href.length > 2048) throw new Error("정규화된 사이트 URL은 최대 2,048자입니다.");
  return url.href;
}

export function validCode(value: string): boolean {
  return /^[A-Za-z0-9^][A-Za-z0-9.^-]{0,19}$/.test(value);
}

export function validateData(value: unknown): HubData {
  if (!record(value) || value.version !== 1 || !Array.isArray(value.sites) || !Array.isArray(value.stocks)
    || value.sites.length > 2000 || value.stocks.length > 2000) {
    throw new Error("지원하지 않는 백업 형식입니다. version: 1, sites, stocks 배열이 필요합니다 (각 최대 2,000개).");
  }
  const ids = new Set<string>();
  const codes = new Set<string>();
  function base(item: unknown): asserts item is Record<string, unknown> & { id: string; name: string; category: string; favorite: boolean } {
    if (!record(item) || !text(item.id, 100) || !text(item.name, 100) || !text(item.category, 50)
      || typeof item.favorite !== "boolean" || ids.has(item.id)) {
      throw new Error("항목의 ID·이름·카테고리·즐겨찾기 형식 또는 중복 ID를 확인하세요.");
    }
    ids.add(item.id);
  }
  const sites = value.sites.map((item: unknown): Site => {
    base(item);
    if (!text(item.url, 2048)) throw new Error("사이트 URL이 없거나 너무 깁니다.");
    return { id: item.id, name: item.name, category: item.category, favorite: item.favorite, url: normalizeUrl(item.url) };
  });
  const stocks = value.stocks.map((item: unknown): Stock => {
    base(item);
    if (!text(item.code, 20) || !validCode(item.code) || codes.has(item.code.toUpperCase()) || !text(item.memo, 20000, true)) {
      throw new Error("종목코드 형식·중복 또는 메모를 확인하세요 (메모 최대 20,000자).");
    }
    codes.add(item.code.toUpperCase());
    return { id: item.id, name: item.name, category: item.category, favorite: item.favorite, code: item.code, memo: item.memo };
  });
  const data: HubData = { version: 1, sites, stocks };
  if (new TextEncoder().encode(JSON.stringify(data, null, 2)).byteLength > MAX_BACKUP_BYTES) {
    throw new Error("저장·백업 데이터는 최대 2MB입니다. 메모나 항목 수를 줄여 주세요.");
  }
  return data;
}

export function stockLinks(stock: Stock): { name: string; url: string }[] {
  const query = encodeURIComponent(`${stock.code} ${stock.name}`);
  return [
    ...(/^\d{6}$/.test(stock.code) ? [
      { name: "네이버 증권", url: `https://finance.naver.com/item/main.naver?code=${encodeURIComponent(stock.code)}` },
      { name: "FnGuide", url: `https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?gicode=A${encodeURIComponent(stock.code)}` },
    ] : [
      { name: "Yahoo Finance", url: `https://finance.yahoo.com/quote/${encodeURIComponent(stock.code)}/` },
    ]),
    { name: "TradingView 검색", url: `https://www.tradingview.com/symbols/?query=${query}` },
  ];
}
