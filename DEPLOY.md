# Synthcast — Deployment Guide

Two options: Railway (easier) or Render (more control).
Both have free tiers. Railway is recommended for getting started.

---

## Option 1: Railway (Recommended — 10 minutes)

Railway detects your Dockerfile automatically and deploys instantly.

### Step 1 — Push to GitHub

You need a GitHub account. If you don't have one, sign up at github.com.

Open your terminal in the synthcast folder and run:

```bash
git init
git add .
git commit -m "Initial Synthcast commit"
```

Go to github.com → New repository → name it "synthcast" → Create.

Then run (replace YOUR_USERNAME with your GitHub username):
```bash
git remote add origin https://github.com/YOUR_USERNAME/synthcast.git
git branch -M main
git push -u origin main
```

### Step 2 — Deploy on Railway

1. Go to railway.app → Sign up with GitHub
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your `synthcast` repository
4. Railway detects the Dockerfile automatically
5. Click **Deploy**

### Step 3 — Add PostgreSQL database

In your Railway project:
1. Click **+ New** → **Database** → **PostgreSQL**
2. Railway creates the database and adds `DATABASE_URL` automatically
3. No configuration needed — it just works

### Step 4 — Add Redis

1. Click **+ New** → **Database** → **Redis**
2. Railway adds `REDIS_URL` automatically

### Step 5 — Add your environment variables

In Railway → your API service → **Variables** tab → add these:

```
OPENAI_API_KEY          = sk-...your key...
ELEVENLABS_API_KEY      = ...your key...
STRIPE_SECRET_KEY       = sk_live_...
STRIPE_WEBHOOK_SECRET   = whsec_...
STRIPE_CREATOR_PRICE_ID = price_...
STRIPE_PRO_PRICE_ID     = price_...
CREATOR_NAME            = Louguens
AVATAR_NAME             = AI Louguens
LLM_PROVIDER            = openai
MEMORY_BACKEND          = redis
SIMULATION_MODE         = false
```

### Step 6 — Get your live URL

Railway gives you a URL like:
```
https://synthcast-production.up.railway.app
```

Test it:
```
https://your-url.railway.app/health
https://your-url.railway.app/docs
```

### Step 7 — Update Stripe webhook

In Stripe dashboard → Webhooks → update the endpoint URL to:
```
https://your-url.railway.app/billing/webhook
```

---

## Option 2: Render (Free tier, always-on)

Render's free tier sleeps after 15 minutes of inactivity.
Upgrade to $7/mo for always-on.

### Step 1 — Push to GitHub (same as Railway Step 1)

### Step 2 — Deploy on Render

1. Go to render.com → Sign up with GitHub
2. Click **New** → **Web Service**
3. Connect your `synthcast` repository
4. Settings:
   - **Name**: synthcast-api
   - **Environment**: Docker
   - **Region**: Oregon (US West)
   - **Branch**: main
5. Click **Create Web Service**

### Step 3 — Add PostgreSQL

1. Render dashboard → **New** → **PostgreSQL**
2. Name: synthcast-db
3. Free tier → Create
4. Copy the **Internal Database URL**
5. Add to your web service environment variables as `DATABASE_URL`

### Step 4 — Add environment variables

Same variables as Railway Step 5.

### Step 5 — Get your URL

Render gives you:
```
https://synthcast-api.onrender.com
```

---

## After deployment — update your .env locally

Add your live server URL so your local listener talks to the cloud:

```
SYNTHCAST_API_URL=https://your-url.railway.app
```

Now your laptop runs the TikTok/Twitch listeners locally,
but the AI brain runs in the cloud 24/7.

---

## Updating the server

Whenever you make changes, push to GitHub:

```bash
git add .
git commit -m "Your change description"
git push
```

Railway and Render redeploy automatically. Takes about 2 minutes.

---

## Checking if it's working

Open your browser:

```
https://your-url/health
```

Should return:
```json
{"status": "ok", "version": "0.1.0", "is_live": false}
```

```
https://your-url/docs
```

Shows all API endpoints — interactive, click to test any of them.

---

## Cost breakdown (Railway)

| Service        | Cost          |
|----------------|---------------|
| API server     | ~$5/mo        |
| PostgreSQL     | ~$5/mo        |
| Redis          | ~$3/mo        |
| **Total**      | **~$13/mo**   |

Railway gives $5 free credit/month — so your first month is ~$8.
Way cheaper than any hosting alternative at this stage.

---

## Domain setup (optional)

Once deployed, point synthcast.io to your Railway/Render URL:

1. Railway → your service → **Settings** → **Custom Domain**
2. Add `synthcast.io` and `api.synthcast.io`
3. In Cloudflare DNS → add CNAME record:
   - Name: `api`
   - Target: your Railway URL

Your API is now live at `https://api.synthcast.io`
