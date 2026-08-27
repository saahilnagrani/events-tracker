-- Events Tracker: everything the app shows
--
-- Run this once in the Supabase SQL editor (Dashboard -> SQL -> New query -> Run).
-- It is written to be re-runnable, so running it again after an edit is safe.
--
-- The security model in one line: the published page carries no data at all, so what
-- protects the events, the calendar and the checklists is this file. Nothing is
-- readable without a signed-in session whose email appears in allowed_emails.
--
-- The daily workflow writes with the service_role key, which bypasses Row Level
-- Security by design. That key lives in a GitHub Actions secret and must never reach
-- the repository or the page.

-- ---------------------------------------------------------------- who is allowed in

create table if not exists public.allowed_emails (
  email      text primary key,
  note       text,
  added_at   timestamptz not null default now()
);

comment on table public.allowed_emails is
  'Allowlist of people who may see anything at all. Add a row before that person '
  'signs in; signing in alone grants nothing.';

-- ---------------------------------------------------------------- the daily dataset

-- Two rows, rewritten every morning: 'events' is the scrape, 'viability' is the
-- scored calendar. One row each rather than a row per event, because the app reads
-- the whole thing at once and never queries inside it.
create table if not exists public.datasets (
  key          text primary key,
  payload      jsonb not null,
  generated    date,
  updated_at   timestamptz not null default now()
);

comment on table public.datasets is
  'Machine-written. The workflow replaces these rows; nobody edits them by hand, and '
  'the app never writes here.';

-- ---------------------------------------------------------------- the checklists

-- Seeded once from the workbook, then owned by whoever is using the app. The daily
-- workflow deliberately does not touch this table: it holds edits, and a nightly
-- overwrite would erase them.
create table if not exists public.checklists (
  id           text primary key,
  doc          jsonb not null,
  updated_at   timestamptz not null default now(),
  updated_by   text
);

comment on table public.checklists is
  'One row per checklist. `doc` is the whole document: title, setup fields, tasks, '
  'statuses, and anything added from the app.';

create or replace function public.touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

drop trigger if exists checklists_touch on public.checklists;
create trigger checklists_touch
  before update on public.checklists
  for each row execute function public.touch_updated_at();

drop trigger if exists datasets_touch on public.datasets;
create trigger datasets_touch
  before update on public.datasets
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------- access rules

alter table public.allowed_emails enable row level security;
alter table public.datasets       enable row level security;
alter table public.checklists     enable row level security;

-- security definer so the check can read the allowlist even though the caller cannot.
-- search_path is pinned: without it, a caller could shadow the table name.
create or replace function public.is_allowed() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.allowed_emails
    where lower(email) = lower(coalesce(auth.jwt() ->> 'email', ''))
  );
$$;

revoke all on function public.is_allowed() from public;
grant execute on function public.is_allowed() to authenticated;

-- The dataset is read-only to everybody who signs in. There is no insert or update
-- policy on purpose: the only writer is the workflow, and it uses the service_role
-- key, which is not subject to these policies.
drop policy if exists datasets_read on public.datasets;
create policy datasets_read on public.datasets
  for select to authenticated using (public.is_allowed());

drop policy if exists checklists_read   on public.checklists;
drop policy if exists checklists_insert on public.checklists;
drop policy if exists checklists_update on public.checklists;

create policy checklists_read on public.checklists
  for select to authenticated using (public.is_allowed());

create policy checklists_insert on public.checklists
  for insert to authenticated with check (public.is_allowed());

create policy checklists_update on public.checklists
  for update to authenticated using (public.is_allowed())
                          with check (public.is_allowed());

-- Deliberately no delete policy: nothing in the app deletes a checklist, so the
-- database should not offer it either.

-- A signed-in person may confirm their own allowlist row and nothing else, so the
-- app can tell "not signed in" apart from "signed in but not invited".
drop policy if exists allowlist_read_self on public.allowed_emails;
create policy allowlist_read_self on public.allowed_emails
  for select to authenticated
  using (lower(email) = lower(coalesce(auth.jwt() ->> 'email', '')));

-- ---------------------------------------------------------------- retired

-- An earlier version of this file kept only checklist edits, while the tasks
-- themselves shipped inside the page. That is what put an artist fee and two
-- counterparties on a public URL, so the table is gone and the content lives in
-- public.checklists above.
drop table if exists public.checklist_state;

-- ---------------------------------------------------------------- invite yourself

-- Replace with the real addresses, then run. Anyone not listed here can sign in and
-- still see nothing, which is the intended behaviour.
insert into public.allowed_emails (email, note) values
  ('you@example.com', 'owner')
on conflict (email) do nothing;
