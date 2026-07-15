# CampusMEET Backend

Flask API for CampusMEET (USTED), talking to Supabase over its REST/Auth
HTTP API. Every request forwards the user's own JWT so Postgres Row-Level
Security enforces the real rules — this backend has no service-role
override and can't bypass RLS even if it wanted to.

---

## 1. Supabase setup (do this first)

1. In your Supabase project, go to **SQL Editor** and confirm
   `db/schema.sql` has already been applied (you've done this — the
   tables `blocks`, `comments`, `hidden_posts`, `posts`, `reactions`,
   `reports`, `universities`, `users` and the `feed` view should all
   exist). If you ever need to start clean, run `db/reset.sql` first,
   then `db/schema.sql`.

2. **Turn off email confirmation** — this is what makes signup
   bottleneck-free. Go to **Authentication → Providers → Email** and
   disable "Confirm email." Without this, Supabase will require an
   email click before a session is returned, which is the OTP-style
   friction you explicitly said not to have.

3. Grab two values from **Project Settings → API**:
   - `Project URL` → this is `SUPABASE_URL`
   - `anon public` key → this is `SUPABASE_ANON_KEY`

   You will need both in step 3 below.

4. Manually promote yourself to admin so the "Verify USTED" endpoints
   work for your account:
   ```sql
   update users set role = 'admin' where student_id_number = 'YOUR_ID_HERE';
   ```

---

## 2. Push this to GitHub

From inside this `backend/` folder:

```bash
git init
git add .
git commit -m "CampusMEET backend — initial build"
git branch -M main
git remote add origin https://github.com/deeeltig-sys/campusmeet-backend.git
git push -u origin main
```

If `campusmeet-backend` doesn't exist yet on GitHub, create it first at
github.com/new under the `deeeltig-sys` account (keep it private if you'd
rather not expose the code publicly), then run the commands above.

`.gitignore` already excludes `.env` — never commit real Supabase keys.

---

## 3. Deploy to Render

**Option A — using render.yaml (fastest):**
1. Go to [render.com/dashboard](https://dashboard.render.com) → **New → Blueprint**.
2. Connect the `campusmeet-backend` GitHub repo.
3. Render reads `render.yaml` automatically and creates the service.
4. It will prompt you for `SUPABASE_URL` and `SUPABASE_ANON_KEY` (marked
   `sync: false` in the blueprint) — paste in the values from step 1.3.
5. Deploy.

**Option B — manual setup:**
1. **New → Web Service** → connect the repo.
2. Root Directory: `backend` (leave blank if this repo *is* the backend folder itself).
3. Runtime: Python 3.
4. Build Command: `pip install -r requirements.txt`
5. Start Command: `gunicorn app:app`
6. Add environment variables:
   - `SUPABASE_URL`
   - `SUPABASE_ANON_KEY`
   - `DEFAULT_UNIVERSITY_CODE` = `USTED`
   - `CORS_ORIGINS` = `*` for now — tighten this once the Capacitor app
     and any web frontend have real origins.
   - `FLASK_ENV` = `production`
7. Create Web Service.

Render will give you a URL like `https://campusmeet-backend.onrender.com`
— that's your `API_BASE_URL` for the frontend and the Capacitor shell.

Note: Render's free tier spins the service down after inactivity, so the
first request after idle time takes a few seconds to wake it up. Fine
for testing; worth upgrading before real launch traffic.

---

## 4. Quick smoke test

```bash
curl https://your-app.onrender.com/
# {"status": "ok", "service": "campusmeet-backend"}

curl -X POST https://your-app.onrender.com/api/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"testpass123","full_name":"Test Student","student_id_number":"5212345678"}'
# returns an access_token if email confirmation is off (step 1.2)

curl https://your-app.onrender.com/api/posts/feed
# []  (empty feed, expected — no posts yet)
```

---

## API reference

| Method | Path                              | Auth        | Notes |
|--------|------------------------------------|-------------|-------|
| POST   | /api/auth/signup                  | none        | open signup |
| POST   | /api/auth/login                   | none        | |
| GET    | /api/auth/me                      | student     | |
| GET    | /api/posts/feed                   | none        | ranked, active posts only |
| POST   | /api/posts                        | student     | |
| GET    | /api/posts/:id                    | none        | |
| PATCH  | /api/posts/:id                    | author      | edit content, or `{"delete": true}` to soft-delete |
| POST   | /api/posts/:id/view                | none        | |
| POST   | /api/posts/:id/search-hit          | none        | |
| POST   | /api/posts/:id/reactions           | student     | body: `{"type": "fire"}` |
| DELETE | /api/posts/:id/reactions           | student     | |
| GET    | /api/profile/:id                   | none        | |
| PATCH  | /api/profile/me                    | student     | `full_name`, `avatar_url` |
| GET    | /api/admin/users                   | staff       | `?verified=false` for the pending queue |
| POST   | /api/admin/users/:id/verify         | staff       | the "Verify USTED" button |
| POST   | /api/admin/users/:id/unverify       | staff       | |
| GET    | /api/admin/reports                 | staff       | |
| PATCH  | /api/admin/reports/:id              | staff       | body: `{"status": "actioned"}` |

All authenticated routes expect `Authorization: Bearer <access_token>`
from the login/signup response.
