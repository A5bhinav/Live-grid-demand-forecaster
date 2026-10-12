import { Empty } from "./ui";

export function Unconfigured() {
  return (
    <section className="card">
      <Empty title="Not connected to a database">
        Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY for this deployment, then
        redeploy.
      </Empty>
    </section>
  );
}
