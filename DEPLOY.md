# 🌐 CrimeWatch — Deployment Guide (Google Cloud Run)

Google Cloud Run is **free for the first 2 million requests/month** and gives you a permanent
public URL like `https://crimewatch-abc123-uc.a.run.app` that anyone in the world can access.

---

## 📋 Prerequisites

1. A **Google account** (Gmail)
2. **Google Cloud SDK** installed on your PC
3. **Docker** installed (optional — Cloud Run can build without it)

---

## 🚀 Step-by-Step Deployment

### Step 1 — Create a Google Cloud Project

1. Go to: **https://console.cloud.google.com**
2. Click **"Select a project"** → **"New Project"**
3. Name it: `crimewatch` → Click **Create**
4. Enable **billing** (required, but free tier covers everything)
   - Go to Billing → Link a billing account (you won't be charged for free tier usage)

---

### Step 2 — Install Google Cloud SDK

**Windows:**
Download from: https://cloud.google.com/sdk/docs/install
Run the installer → follow prompts → restart terminal

**Mac:**
```bash
brew install --cask google-cloud-sdk
```

**Ubuntu/Linux:**
```bash
curl https://sdk.cloud.google.com | bash
exec -l $SHELL
```

---

### Step 3 — Authenticate & Set Project

Open terminal/command prompt:

```bash
# Login to Google
gcloud auth login

# Set your project (replace YOUR_PROJECT_ID with what you named it)
gcloud config set project YOUR_PROJECT_ID

# Enable required APIs
gcloud services enable run.googleapis.com
gcloud services enable cloudbuild.googleapis.com
gcloud services enable artifactregistry.googleapis.com
```

---

### Step 4 — Deploy to Cloud Run

Navigate to your crimewatch folder, then run:

```bash
cd crimewatch

gcloud run deploy crimewatch \
  --source . \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars "SECRET_KEY=CHANGE_THIS_TO_RANDOM_STRING,SUPERADMIN_PASSWORD=YourStrongPassword123"
```

**Replace:**
- `CHANGE_THIS_TO_RANDOM_STRING` → any long random string (e.g. `xK9mP2qR8nL4wZ7j`)
- `YourStrongPassword123` → your desired admin password

When prompted: **"Allow unauthenticated invocations?"** → type **y**

⏳ Wait ~3 minutes for build and deploy...

**You'll get a URL like:**
```
✅ Service URL: https://crimewatch-abc123-uc.a.run.app
```

**That's your public website! Share it with anyone.**

---

### Step 5 — First Login

1. Go to: `https://your-url.a.run.app/admin/login`
2. Username: `superadmin`
3. Password: whatever you set as `SUPERADMIN_PASSWORD`
4. **Change your password immediately** after first login!

---

## 👥 Adding More Admins

Once logged in as superadmin:

1. Go to **Admin Panel** → click **"👥 Admin Users"** tab
2. Fill in the **Add New Admin** form
3. Choose role:
   - **Admin** — can verify/remove crime reports only
   - **Super Admin** — full access + can manage other admins
4. Share the username & password with your team member
5. They log in at `/admin/login`

---

## 🔄 Updating the App Later

After making code changes:

```bash
gcloud run deploy crimewatch --source .
```

That's it — it rebuilds and redeploys automatically.

---

## ⚠️ Important Notes

### Database Persistence
Cloud Run is **stateless** — the SQLite database resets when the container restarts.
For a production site with persistent data, upgrade to **Cloud SQL (PostgreSQL)**:

```bash
# Quick upgrade path when ready:
# 1. Create Cloud SQL instance in Google Console
# 2. Install psycopg2: pip install psycopg2-binary
# 3. Set DATABASE_URL env var on Cloud Run
```

For **testing/demo purposes**, the current SQLite setup works perfectly fine.

### Custom Domain (Optional)
To use your own domain like `crimewatch.in`:
1. Go to Cloud Run → your service → **Custom Domains**
2. Click **Add Mapping** → enter your domain
3. Update your domain's DNS records as shown

### Cost
- **Free tier:** 2 million requests/month, 360,000 GB-seconds compute
- A typical community site with ~1000 visits/day stays well within free tier
- Set a **budget alert** at $1 in Google Cloud Billing to be safe

---

## 🛡️ Admin Roles Summary

| Feature | Admin | Super Admin |
|---------|-------|-------------|
| Verify crime reports | ✅ | ✅ |
| Remove fake reports | ✅ | ✅ |
| View community tips | ✅ | ✅ |
| Add new admins | ❌ | ✅ |
| Deactivate admins | ❌ | ✅ |
| Delete admins | ❌ | ✅ |
| Change own password | ✅ | ✅ |

---

## 🆘 Troubleshooting

**"Permission denied" error:**
```bash
gcloud auth application-default login
```

**Build fails:**
```bash
# Check Docker is running, or use Cloud Build directly:
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/crimewatch
gcloud run deploy crimewatch --image gcr.io/YOUR_PROJECT_ID/crimewatch --platform managed --region us-central1 --allow-unauthenticated
```

**App crashes on startup:**
```bash
# View logs
gcloud run logs read --service crimewatch --region us-central1
```
