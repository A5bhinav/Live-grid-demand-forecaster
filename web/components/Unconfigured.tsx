export function Unconfigured() {
  return (
    <div className="card">
      <p className="empty">
        The dashboard is not connected to a database. Set NEXT_PUBLIC_SUPABASE_URL and
        NEXT_PUBLIC_SUPABASE_ANON_KEY and rebuild.
      </p>
    </div>
  );
}
