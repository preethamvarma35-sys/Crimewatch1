# 🗺 Google Maps API Key Setup

You need a FREE Google Maps API key to use the map.
Google gives $200 free credits every month — more than enough for a community site.

---

## Step 1 — Get Your API Key (5 minutes)

1. Go to: https://console.cloud.google.com
2. Sign in with your Google account
3. Click **"Select a project"** → **"New Project"** → name it `crimewatch` → **Create**
4. In the left menu go to: **APIs & Services → Library**
5. Search and **Enable** these 3 APIs one by one:
   - ✅ **Maps JavaScript API**
   - ✅ **Places API**
   - ✅ **Geocoding API**
6. Go to: **APIs & Services → Credentials**
7. Click **"+ Create Credentials"** → **"API Key"**
8. Copy the key — it looks like: `AIzaSyD-xxxxxxxxxxxxxxxxxxxxxxxxxxxx`

---

## Step 2 — Add Key to the Project

Open these 2 files and replace `YOUR_GOOGLE_MAPS_API_KEY` with your actual key:

### In `templates/map.html` (line near the bottom):
```html
<script
  src="https://maps.googleapis.com/maps/api/js?key=YOUR_GOOGLE_MAPS_API_KEY&callback=initMap&v=weekly"
```
Change to:
```html
<script
  src="https://maps.googleapis.com/maps/api/js?key=AIzaSyD-your-actual-key&callback=initMap&v=weekly"
```

### In `templates/report.html` (line near the bottom):
```html
<script
  src="https://maps.googleapis.com/maps/api/js?key=YOUR_GOOGLE_MAPS_API_KEY&libraries=places&callback=initPickerMap&v=weekly"
```
Change to:
```html
<script
  src="https://maps.googleapis.com/maps/api/js?key=AIzaSyD-your-actual-key&libraries=places&callback=initPickerMap&v=weekly"
```

---

## Step 3 — Restrict Your Key (Important for Security)

1. In Google Cloud Console → **Credentials** → click your API key
2. Under **"Application restrictions"** → select **"HTTP referrers"**
3. Add your website URL:
   - For local: `http://localhost:5000/*`
   - For Render: `https://your-app.onrender.com/*`
4. Click **Save**

This prevents others from stealing and using your key.

---

## Step 4 — Set Billing Alert

1. Go to **Billing → Budgets & Alerts**
2. Create a budget alert at **$1**
3. You'll get an email if you somehow exceed the free tier
   (Very unlikely for a community crime reporting site)

---

## ✅ Done!

Once the key is added, your map will show:
- 🗺 Roadmap view (clear street names)
- 🛰 Satellite view (Google's sharp aerial imagery)
- 🛰+🗺 Hybrid view (satellite with street labels)
- 🏔 Terrain view (elevation and geography)
- 🔍 Location search bar on the map
- 📍 Auto-complete address search when reporting crimes
