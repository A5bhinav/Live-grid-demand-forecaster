-- An outside trigger for the GitHub workflows, for when GitHub's own scheduler delays or
-- drops runs: Postgres calls GitHub's workflow-dispatch API on a timetable.
--
-- Supabase only. It needs pg_cron, pg_net and Vault, which a plain Postgres does not have,
-- so this is not one of the numbered migrations. Apply it with
--   uv run python -m gridcast.jobs.scheduler install
-- which also stores the GitHub token in Vault. Safe to apply again.

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- Kept out of `public`, which Supabase serves over its API, so nobody can trigger a
-- workflow by calling this function with the publishable key.
create schema if not exists scheduler;
revoke all on schema scheduler from public;

create or replace function scheduler.dispatch_workflow(workflow text) returns bigint
language plpgsql set search_path = '' as $$
declare
  token text;
begin
  select decrypted_secret into token
  from vault.decrypted_secrets where name = 'github_dispatch_token';
  if token is null then
    raise exception 'github_dispatch_token is not stored in Vault';
  end if;
  return net.http_post(
    url := 'https://api.github.com/repos/A5bhinav/Live-grid-demand-forecaster/actions/workflows/'
           || workflow || '/dispatches',
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || token,
      'Accept', 'application/vnd.github+json',
      'X-GitHub-Api-Version', '2022-11-28',
      'User-Agent', 'gridcast-scheduler',
      'Content-Type', 'application/json'
    ),
    body := jsonb_build_object('ref', 'main')
  );
end $$;
revoke all on function scheduler.dispatch_workflow(text) from public;

-- Times are UTC and match the workflows' own schedules, which stay in place as a backup.
-- Every job is safe to run twice. Scheduling an existing name replaces it.
select cron.schedule('gridcast-ingest', '17 */3 * * *', $$select scheduler.dispatch_workflow('ingest.yml')$$);
select cron.schedule('gridcast-issue', '23 16 * * *', $$select scheduler.dispatch_workflow('issue.yml')$$);
select cron.schedule('gridcast-issue-backup', '53 17 * * *', $$select scheduler.dispatch_workflow('issue.yml')$$);
select cron.schedule('gridcast-score', '41 17 * * *', $$select scheduler.dispatch_workflow('score.yml')$$);
select cron.schedule('gridcast-monitor', '7 19 * * *', $$select scheduler.dispatch_workflow('monitor.yml')$$);
select cron.schedule('gridcast-retrain', '47 12 * * 1', $$select scheduler.dispatch_workflow('retrain.yml')$$);
