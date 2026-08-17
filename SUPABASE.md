# Shared checklists across devices

The events list and the calendar are generated daily and are the same for everyone, so
they need no account. Checklists are different: they hold your edits, and you want them
on your laptop and your phone. That needs somewhere to put them.

Until the steps below are done, nothing changes. Checklists save to the browser you are
using, exactly as they do now, and the app never contacts anything.

## What you are setting up

Supabase gives you a hosted Postgres database, email sign-in, and an HTTP API the page
can call directly. The free tier covers this comfortably.

Two things are worth understanding before you start, because they decide whether this is
safe:

**The anon key is public.** It ships inside `index.html`, which is served from a public
GitHub Pages site. That is how Supabase is designed to work, and it is not a mistake.
What stops a stranger reading your checklists is not secrecy of that key, it is Row
Level Security: the policies in `supabase/schema.sql` refuse every read and write unless
the caller is signed in *and* their email is in the `allowed_emails` table.

**Signing in is not the same as being allowed in.** Anyone can request a sign-in link
for their own address. They will then be authenticated and still see nothing, because
they are not on the allowlist. Adding someone is a deliberate act: one row in one table.

## Steps

### 1. Create the project

1. Sign up at [supabase.com](https://supabase.com) and create a new project.
2. Pick a region near you; `eu-central` or `ap-south` are both fine from Dubai.
3. Wait for it to finish provisioning, a minute or two.

### 2. Create the tables and the rules

1. In the dashboard, open **SQL Editor** and click **New query**.
2. Paste the whole of `supabase/schema.sql` from this repository.
3. Before running, edit the last statement so it carries your real address instead of
   `you@example.com`.
4. Click **Run**. It is safe to run again later if you change something.

### 3. Invite the other people

For each person, run this in the SQL editor:

```sql
insert into public.allowed_emails (email, note)
values ('sunny@example.com', 'sales and ops')
on conflict (email) do nothing;
```

To remove someone's access later:

```sql
delete from public.allowed_emails where email = 'sunny@example.com';
```

That takes effect on their next request. They stay able to sign in; they just stop being
able to see anything.

### 4. Allow the site to receive the sign-in link

1. Open **Authentication -> URL Configuration**.
2. Set **Site URL** to `https://saahilnagrani.github.io/events-tracker/`.
3. Add the same address under **Redirect URLs**.

Miss this and the magic link will send people to a page that cannot complete the
sign-in.

### 5. Point the app at the project

1. Open **Project Settings -> API**.
2. Copy the **Project URL** and the **anon public** key.
3. Put them in `data/backend.json`:

```json
{
  "supabase_url": "https://abcdefgh.supabase.co",
  "supabase_anon_key": "eyJhbGciOi..."
}
```

4. Commit and push. The daily workflow rebuilds the site, or run
   `python src/build_site.py` yourself and push `docs/`.

`python src/build_site.py` prints which mode it built, so you can confirm it took:

```
checklist sync: Supabase https://abcdefgh.supabase.co
```

### 6. Sign in

Open the Checklist tab. There is now a row at the top offering to email you a link.
Enter your address, click the button, open the mail, and you land back on the site
signed in. Repeat once on each device.

## How the syncing behaves

- **Your browser is still the working copy.** Every edit saves locally first, so the app
  stays usable with no signal and offline, exactly as before.
- **Changes push about a second after you stop typing**, so a burst of edits is one write
  rather than twenty.
- **On opening the page, whichever copy is newer wins.** If the server has been changed
  since your device last wrote, your device adopts it; if your device is ahead, it
  pushes.
- **Conflicts are per checklist, not per task.** If two people edit the same checklist at
  the same moment, the later write wins the whole document, and the row records who
  wrote it. With a handful of people that is a rare and visible loss rather than silent
  corruption. If it starts happening, the fix is to store one row per task, which is a
  contained change.
- **If the server cannot be reached**, the app says so and carries on locally. Nothing is
  lost; the next successful write catches up.

## If something does not work

| What you see | Likely cause |
|---|---|
| No sign-in row on the Checklist tab | `data/backend.json` is empty, or the site was not rebuilt after filling it in |
| "Could not send the link" | Project URL or anon key is wrong, or the project is paused |
| The link opens the site but you are not signed in | Redirect URL not added in step 4 |
| "Signed in, but this address is not on the allowlist" | Exactly what it says: add the row from step 3 |
| "Offline; kept on this device" | No connection, or the project is paused. Free projects pause after a week of inactivity; open the dashboard to resume |

## What has not been tested

This was written without a live project to test against, because creating one needs your
account. The schema, the policies and the client are all written carefully, but the first
real sign-in is the first time any of it runs against Supabase. Expect one or two small
things to need correcting, and tell me what the error says.
