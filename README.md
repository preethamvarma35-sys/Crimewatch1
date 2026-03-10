# CrimeWatch — Community Safety Platform

A full-stack web application for community crime reporting with photo upload support.

## Quick Start (VSCode)

```bash
# 1. Install dependency
pip install flask

# 2. Run the app
python app.py

# 3. Open browser at:
http://localhost:5000
```

## Pages

| Page | URL | Description |
|------|-----|-------------|
| Dashboard | `/` | Stats, alerts, recent crimes |
| Crime Map | `/map` | Interactive map with markers |
| Browse Crimes | `/crimes` | Search & filter crimes |
| Crime Detail | `/crimes/<id>` | Photos, suspects, tips |
| Report Crime | `/report` | Report with photo upload |
| Stay Safe | `/safety` | Tips & emergency numbers |
| Admin Panel | `/admin` | Verify reports, manage cases |

## Photo Upload Features

- **On Report Form** — Upload multiple photos when reporting a crime
- **On Crime Detail Page** — Upload additional photos after reporting  
- Drag & drop or click-to-browse
- Supports JPG, PNG, GIF, WEBP (max 16MB each)
- Click any photo to open full-size lightbox view

## Database

SQLite database (`crimewatch.db`) is auto-created on first run. Starts empty — all data is entered by users.
