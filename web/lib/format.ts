import { TIME_ZONE } from "./data";

export const MODEL_NAMES: Record<string, string> = {
  seasonal_naive: "Seasonal naive",
  gbm_calendar_lags: "LightGBM: calendar and lags",
  gbm_plus_temperature: "LightGBM: plus temperature",
  gbm_full: "LightGBM: full",
  operator: "Operator forecast",
};
export const MODEL_ORDER = Object.keys(MODEL_NAMES);

export const FLAG_NAMES: Record<string, string> = {
  missing: "Missing",
  spike: "Spike",
  repeated_value: "Repeated value",
  global_outlier: "Global outlier",
  anomalous_region: "Anomalous region",
  local_outlier_low: "Local outlier (low)",
  local_outlier_high: "Local outlier (high)",
  non_positive: "Zero or negative",
};

const dayFormat = new Intl.DateTimeFormat("en-CA", { timeZone: TIME_ZONE });
const hourFormat = new Intl.DateTimeFormat("en-GB", {
  timeZone: TIME_ZONE,
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
const stampFormat = new Intl.DateTimeFormat("en-US", {
  timeZone: TIME_ZONE,
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});
const longDay = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  weekday: "short",
  month: "short",
  day: "numeric",
});

/** A period is stamped at the end of its hour; it belongs to the hour that started 60 minutes earlier. */
export function hourStart(periodUtc: string): Date {
  return new Date(new Date(periodUtc).getTime() - 3_600_000);
}
export const localDay = (date: Date) => dayFormat.format(date);
export const localHour = (date: Date) => hourFormat.format(date);
export const stamp = (iso: string | null) => (iso ? stampFormat.format(new Date(iso)) : "none yet");
export const dayLabel = (isoDay: string) => longDay.format(new Date(`${isoDay}T00:00:00Z`));

export const percent = (value: number | null | undefined, digits = 2) =>
  value == null ? "n/a" : `${value.toFixed(digits)}%`;
export const megawatts = (value: number | null | undefined) =>
  value == null ? "n/a" : `${Math.round(value).toLocaleString("en-US")} MW`;
export const gigawatts = (value: number | null | undefined) =>
  value == null ? "n/a" : `${(value / 1000).toFixed(1)} GW`;

export function hoursAgo(iso: string | null, now: Date): string {
  if (!iso) return "never";
  const hours = Math.round((now.getTime() - new Date(iso).getTime()) / 3_600_000);
  if (hours < 0) return `in ${-hours} h`;
  return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} days ago`;
}
