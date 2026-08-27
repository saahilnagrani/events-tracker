# The database

Everything the app shows lives in Supabase: the listings, the scored calendar and the
checklists. The published page holds none of it. That is the point of the sign-in — a
static page cannot withhold what it has already handed to the browser, so if a login is
to mean anything, the data has to arrive after it.

Until the steps below are done the app shows a sign-in screen and nothing else.

## Two things worth understanding first

**The anon key is public.** It ships inside `index.html`, which is served from a public
GitHub Pages site. That is how Supabase is designed to work. What stops a stranger
reading anything is not secrecy of that key, it is Row Level Security: the policies in
`supabase/schema.sql` refuse every read unless the caller is signed in *and* their email
is in `allowed_emails`.

**The service_role key is not public and never goes in the repository.** It bypasses
every policy, which is exactly why the daily workflow uses it to write the dataset. It
lives in one place: a GitHub Actions secret.

## Steps

### 1. The project

You already have one, restored. If you ever start again: any region near you,
`eu-central` or `ap-south` are both fine from Dubai.

### 2. The tables and the rules

1. Dashboard → **SQL Editor** → **New query**.
2. Paste the whole of `supabase/schema.sql`.
3. Edit the last statement to carry your real address instead of `you@example.com`.
4. **Run**. Safe to run again later.

That creates three tables. `allowed_emails` is who may see anything. `datasets` holds
the daily payloads, machine-written and read-only to everyone else. `checklists` holds
the documents, seeded once and then owned by whoever is using the app.

### 3. Invite the other people

```sql
insert into public.allowed_emails (email, note)
values ('sunny@example.com', 'sales and ops')
on conflict (email) do nothing;
```

To remove someone: `delete from public.allowed_emails where email = '...';`. It takes
effect on their next request. They can still sign in; they just stop seeing anything.

### 4. How accounts get made

Under **Authentication → Providers → Email**:

- **Confirm email** — on by default. Creating an account then sends a confirmation mail
  and sign-in is refused until the link is clicked.
- **Allow new users to sign up** — leave it on and anyone can create an account and
  still see nothing, because of the allowlist. Turn it off and you add each person
  under **Authentication → Users → Add user** with a password you set.

Under **Authentication → URL Configuration**, set **Site URL** to
`https://saahilnagrani.github.io/events-tracker/` and add the same address under
**Redirect URLs**. Confirmation and password-reset links need it; ordinary password
sign-in does not.

### 5. Point the app at the project

**Project Settings → API**, copy the **Project URL** and the public client key (newer
projects call it **Publishable key**, starting `sb_publishable_`; older ones **anon
public**, a long JWT). Put both in `data/backend.json`:

```json
{
  "supabase_url": "https://abcdefgh.supabase.co",
  "supabase_anon_key": "sb_publishable_..."
}
```

This one *is* committed. It is the public key.

### 6. The secret the workflow needs

**Project Settings → API → service_role**. Copy it, then in GitHub:
**Settings → Secrets and variables → Actions → New repository secret**, named
exactly `SUPABASE_SERVICE_KEY`.

Nothing else needs it. Never paste it into a file in the repository.

### 7. Put the checklists in

The workbook import no longer writes into the repository. From a checkout, with the
service key exported for that one command:

```
export SUPABASE_SERVICE_KEY=...
python src/import_checklist.py 'Ghazal Night.xlsx' --upload
```

It refuses to overwrite a checklist that is already in the database, because that table
holds ticked boxes and typed-in fields by then. `python src/publish.py --dump-checklists
backup.json` takes a copy; keep it out of the repository.

### 8. Sign in

Open the site. It asks for an email and a password, on every tab, because there is
nothing to show until it has them. **Create an account** the first time, or make the
account in the dashboard if you turned sign-up off in step 4.

## How it behaves once it is running

- **Signing in fetches everything**: one row for the calendar and the listings, one row
  per checklist. It is cached in that browser so the app opens instantly next time and
  works with no signal.
- **Signing out clears that cache** and wipes what was on screen. On a shared machine
  that matters, so it is not just a hidden panel.
- **Checklist edits save locally first**, then push about a second after you stop
  typing. Whichever copy is newer wins per checklist, and the row records who wrote it.
- **The daily workflow never writes the checklists table.** It writes `datasets` only.
  A nightly overwrite from a file would erase your edits.
- **If the database cannot be reached** and this device has a cached copy, the app keeps
  working from it and says so. With no cached copy there is nothing to show, and it says
  that instead of pretending.

## Keeping the project awake

A free project pauses after seven days with no requests, and Supabase's own guidance is
that a few requests a day is enough to avoid it. The daily workflow makes one before it
does anything else, so even a run that fails later counts as activity.

If it pauses anyway, a ping cannot wake it: press **Restore** in the dashboard, which
takes a couple of minutes and loses nothing. The app names that state rather than
blaming your connection, and the run puts it at the top of its summary.

Two ways it could still pause: GitHub disables a scheduled workflow after 60 days with
no commit to the repository, and the daily run only commits when the shell changes — so
if nothing is committed for two months, push anything to wake the schedule. And if you
would rather not depend on any of it, the Pro plan does not pause.

## If something does not work

| What you see | Likely cause |
|---|---|
| "Sign in to see the tracker" and nothing else | Normal when signed out. If it persists after signing in, read the message under it |
| "Signed in, but this address is not on the allowlist yet" | Add the row from step 3 |
| "This project is paused" | Press Restore in the dashboard |
| "Could not reach the database" | No connection, or the project URL or key is wrong |
| "That email and password do not match an account" | Wrong password, or the account was never created |
| "Confirm your address first" | Email confirmation is on and the link has not been clicked |
| "Signups not allowed for this instance" | Self-serve sign-up is off; add the user in the dashboard |
| The workflow fails on **Pull the previous dataset** | `SUPABASE_SERVICE_KEY` is missing, wrong, or the project is paused |
| The checklist tab is empty though the calendar loads | Nothing seeded yet; see step 7 |

## What has not been tested

The client and the schema are written carefully, and the browser tests run against a
stand-in database that answers exactly as Supabase does, including an empty array for a
caller who is not on the allowlist. What has never run is the real thing: no request in
this repository has ever reached your project. The first sign-in, the first seed and the
first workflow run are the first time any of it meets Postgres. Expect one or two small
corrections, and tell me what the error says.
