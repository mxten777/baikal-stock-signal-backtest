/**
 * Sanitizes system and metric warning messages to ensure operator-friendly presentation
 * without exposing internal dev/step details.
 */
export function sanitizeWarning(warning: string | null | undefined): string {
  if (!warning) return "";
  let text = warning.trim();

  // Replace internal step mentions with operator-friendly terms
  if (/pipeline status is not persisted/i.test(text)) {
    return "Pipeline metadata not available";
  }
  if (/pipeline last_run is not persisted/i.test(text)) {
    return "Run metadata not available";
  }
  if (/shadow ledger file does not exist/i.test(text)) {
    return "Shadow ledger file not found";
  }
  if (/HIGH classification is not present/i.test(text)) {
    return "HIGH classification not available in operational ledger";
  }
  if (/HIGH weakness requires an explicit validation mapping/i.test(text)) {
    return "HIGH weakness mapping not available";
  }
  if (/KOSDAQ weakness is allowed but not normalized/i.test(text)) {
    return "KOSDAQ weakness normalization pending";
  }
  if (/HIGH x KOSDAQ cross metric is unavailable/i.test(text)) {
    return "HIGH × KOSDAQ cross metric unavailable";
  }

  // Generic cleanup of internal STEP references if any
  text = text.replace(/\bin STEP \d+\b/gi, "");
  text = text.replace(/\bSTEP \d+\b/gi, "current baseline");

  return text.trim();
}
