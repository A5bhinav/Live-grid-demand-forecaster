// Server-side reads from the public dashboard views. Nothing here can write.
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

export const TIME_ZONE = "America/Los_Angeles";

export type HourlyRow = {
  period_utc: string;
  forecast_mw: number | null;
  model_version: string | null;
  issued_at: string | null;
  operator_mw: number | null;
  actual_mw: number | null;
  actual_flag: string | null;
};

export type ScoreRow = {
  model_version: string;
  target_date: string;
  hours: number;
  hours_scored: number;
  mape: number | null;
  mae_mw: number | null;
  peak_error_mw: number | null;
  operator_mape: number | null;
  operator_peak_error_mw: number | null;
  other_hours_scored: number;
  other_hours_mape: number | null;
  operator_other_hours_mape: number | null;
};

export type BacktestRow = {
  run_at: string;
  first_month: string;
  last_month: string;
  model: string;
  breakdown: "overall" | "time_of_day" | "season" | "month";
  key: string;
  hours: number;
  mape: number;
  mae_mw: number | null;
  rmse_mw: number | null;
  peak_abs_error_mw: number | null;
  win_rate_vs_operator: number | null;
};

export type FlagRow = { month: string; flag: string; hours: number };

export type Status = {
  latest_demand_period: string | null;
  latest_operator_period: string | null;
  latest_weather_valid_time: string | null;
  latest_forecast_issued_at: string | null;
  latest_forecast_target: string | null;
  days_forecast: number;
  days_scored: number;
  latest_scored_date: string | null;
  active_model: string | null;
  active_model_trained_at: string | null;
  demand_hours: number;
  demand_hours_flagged: number;
};

function client(): SupabaseClient | null {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) return null;
  return createClient(url, key, { auth: { persistSession: false } });
}

async function rows<T>(view: string, order?: string): Promise<T[] | null> {
  const supabase = client();
  if (!supabase) return null;
  let query = supabase.from(view).select("*");
  if (order) query = query.order(order);
  const { data, error } = await query;
  if (error) throw new Error(`${view}: ${error.message}`);
  return data as T[];
}

export const getHourly = () => rows<HourlyRow>("dashboard_hourly", "period_utc");
export const getScores = () => rows<ScoreRow>("dashboard_scores", "target_date");
export const getBacktest = () => rows<BacktestRow>("dashboard_backtest");
export const getFlags = () => rows<FlagRow>("dashboard_flags_monthly", "month");
export const getStatus = async () => (await rows<Status>("dashboard_status"))?.[0] ?? null;
