# Accounts and shared checklists

The events list and the calendar are generated daily and are the same for everyone, so
they need no account: a sign-in there would be a door with nothing behind it. Checklists
are different: they hold your edits, and you want them on your laptop and your phone.
That needs an account and somewhere to put the data.

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

**Signing in is not the same as being allowed in.** Anyone can create an account for
their own address. They will then be authenticated and still see nothing, because they
are not on the allowlist. Adding someone is a deliberate act: one row in one table. If
you would rather nobody could even create an account, step 4 turns self-serve sign-up
off and you make the accounts yourself.

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

### 4. Decide how accounts get made, and where email links land

Sign-in is an email address and a password. Two settings decide how the first one gets
created, both under **Authentication**:

- **Providers -> Email -> Confirm email.** On by default. With it on, creating an
  account sends a confirmation mail and sign-in is refused until the link is clicked.
  With it off, creating an account signs you straight in. On is the safer default; off
  is quicker if the mailbox is awkward.
- **Providers -> Email -> Allow new users to sign up.** Leave it on and anyone can make
  an account (and still see nothing, because of the allowlist). Turn it off and the
  *Create an account* button starts returning "signups not allowed"; you then add each
  person under **Authentication -> Users -> Add user**, setting their password there.

Then, under **URL Configuration**:

1. Set **Site URL** to `https://saahilnagrani.github.io/events-tracker/`.
2. Add the same address under **Redirect URLs**.

Miss that and the confirmation and password-reset links will land on a page that cannot
complete them. Ordinary password sign-in does not need it.

### 5. Point the app at the project

1. Open **Project Settings -> API**.
2. Copy the **Project URL** and the public client key. Newer projects call this the
   **Publishable key** and it starts `sb_publishable_`; older ones call it **anon
   public** and it is a long JWT starting `ey`. Either works. Do **not** use the
   secret or service_role key: that one bypasses every policy below and must never
   ship in a page.
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

The account control is the person icon in the top right of the app, on every tab, not
just the Checklist one. Click it, enter your email and a password, and click **Create an
account** the first time (or make the account yourself in the dashboard if you turned
sign-up off in step 4). After that it is email and password on any device, with no mail
round trip.

**Forgot password** sends a reset link; opening it puts you back on the site with the
new-password field already showing.

The Checklist tab still shows a one-line status of where its data is going, and a way
back into the same dialog.

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

## Keeping the project awake

A free Supabase project pauses after seven days with no requests, and this app only
calls Supabase when somebody opens a checklist. A quiet fortnight would be enough to
put it to sleep, and a paused project has to be restored by hand from the dashboard.

The daily workflow therefore makes one request to the project every morning, before it
does anything else, so even a run that fails later still counts as activity. It reads
the URL and key from `data/backend.json`, and treats 200, 401 and 403 alike: all three
mean the project answered.

If it does pause before a ping lands, a ping cannot wake it: pausing is only undone by
pressing **Restore** in the dashboard, which takes a couple of minutes and loses
nothing. The app says so plainly rather than blaming your connection, and the daily run
puts it at the top of its own summary.

That leaves two ways it could still pause. The workflow itself has to keep running:
GitHub disables a scheduled workflow after 60 days without any commit to the
repository, and the daily run only commits on days when the listings actually move. If
the dataset ever goes two months without a change, push anything to wake the schedule.
And if you would rather not depend on either, the Pro plan does not pause at all.

## If something does not work

| What you see | Likely cause |
|---|---|
| No account icon in the header | `data/backend.json` is empty, or the site was not rebuilt after filling it in |
| "That email and password do not match an account" | Wrong password, or the account was never created |
| "Confirm your address first" | Email confirmation is on and the mail has not been clicked; see step 4 |
| "Signups not allowed for this instance" | Self-serve sign-up is off; add the user in the dashboard |
| "Could not reach the server" | Project URL or key is wrong, the project is paused, or there is no connection |
| A reset link opens the site but nothing happens | Redirect URL not added in step 4 |
| "Signed in, but this address is not on the allowlist" | Exactly what it says: add the row from step 3 |
| "This project is paused" | Exactly that. Open the dashboard, press Restore, wait a couple of minutes. Nothing on your devices is lost |
| "Offline; kept on this device" | No connection. Edits stay local and catch up on the next successful write |

## What has not been tested

This was written without a live project to test against, because creating one needs your
account. The schema, the policies and the client are all written carefully, and the
client is tested against a backend that refuses every request, so the offline and
failure paths are known good. What has never run is the succeeding path: creating an
account, signing in, and a row actually landing in `checklist_state`. Expect one or two
small things to need correcting, and tell me what the error says.
