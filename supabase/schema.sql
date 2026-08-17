-- Events Tracker: shared checklist state
--
-- Run this once in the Supabase SQL editor (Dashboard -> SQL -> New query -> Run).
-- It is written to be re-runnable, so running it again after an edit is safe.
--
-- The security model in one line: the anon key is public and always will be, because
-- it ships inside the page, so nothing may be readable or writable without a signed-in
-- session whose email appears in allowed_emails. Row Level Security is what enforces
-- that; without the policies below, a public anon key is a public database.

-- ---------------------------------------------------------------- who is allowed in

create table if not exists public.allowed_emails (
  email      text primary key,
  note       text,
  added_at   timestamptz not null default now()
);

comment on table public.allowed_emails is
  'Allowlist of people who may read and write checklists. Add a row before that person '
  'signs in; signing in alone grants nothing.';

-- ---------------------------------------------------------------- the data

create table if not exists public.checklist_state (
  id           text primary key,          -- checklist id, e.g. ghazal-night-ranjit-rajwada
  data         jsonb not null default '{}'::jsonb,
  updated_at   timestamptz not null default now(),
  updated_by   text
);

comment on table public.checklist_state is
  'One row per checklist. `data` holds the whole editable state: task statuses, the '
  'show date, the setup fields and any locally added tasks. The imported task list '
  'itself stays in the repository; this table only holds what people change.';

create or replace function public.touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

drop trigger if exists checklist_state_touch on public.checklist_state;
create trigger checklist_state_touch
  before update on public.checklist_state
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------- access rules

alter table public.allowed_emails  enable row level security;
alter table public.checklist_state enable row level security;

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

drop policy if exists checklist_read   on public.checklist_state;
drop policy if exists checklist_insert on public.checklist_state;
drop policy if exists checklist_update on public.checklist_state;

create policy checklist_read on public.checklist_state
  for select to authenticated using (public.is_allowed());

create policy checklist_insert on public.checklist_state
  for insert to authenticated with check (public.is_allowed());

create policy checklist_update on public.checklist_state
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

-- ---------------------------------------------------------------- invite yourself

-- Replace with the real addresses, then run. Anyone not listed here can sign in and
-- still see nothing, which is the intended behaviour.
insert into public.allowed_emails (email, note) values
  ('you@example.com', 'owner')
on conflict (email) do nothing;
