# Phase 1: Quick Start Guide

## Setup (5 minutes)

### 1. Create .env file
```bash
cp .env.example .env
```

Fill in your Cloudinary credentials:
```
CLOUDINARY_CLOUD_NAME=your_name
CLOUDINARY_API_KEY=your_key
CLOUDINARY_API_SECRET=your_secret
```

### 2. MongoDB Setup
```bash
# Make sure MongoDB is running locally
# On macOS:
brew services start mongodb-community

# Test connection:
mongosh
> show databases
> exit()
```

### 3. Install Next.js Dependencies
```bash
cd app
npm install
```

### 4. Start Next.js Server
```bash
cd app
npm run dev
```

The app will run at **http://localhost:3000**

## Phase 1 Features (Ready Now)

✅ **Create Projects** - Name, board, standard, subject, language
✅ **Upload Documents** - PDF + HTML files to Cloudinary
✅ **View Projects** - Dashboard with all projects
✅ **MongoDB Integration** - Data persistence
✅ **REST APIs** - Full CRUD for projects, documents, jobs

## Phase 1 API Endpoints

```
POST   /api/projects                    Create project
GET    /api/projects                    List all projects
GET    /api/projects/:id                Get single project
PUT    /api/projects/:id                Update project
DELETE /api/projects/:id                Delete project

POST   /api/documents                   Create document
GET    /api/documents                   List documents
POST   /api/upload                      Upload file to Cloudinary

POST   /api/jobs                        Start processing job
GET    /api/jobs                        List jobs
```

## Testing Flow

1. Open http://localhost:3000
2. Click "New Project"
3. Fill in project details (e.g., "G08MT101", CBSE, 8, Mathematics)
4. Go to project
5. Upload sample PDF and HTML files
6. Button "Start Processing" appears (links to Phase 2)

## Phase 2 (Python Services)

When ready to start Phase 2:

```bash
cd python-services

# Create virtual environment
python -m venv venv
source venv/bin/activate  # macOS/Linux
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Start Python server
python main.py
```

Python API will run at **http://localhost:8000**

## Key Files

### Frontend (Next.js)
- `app/src/app/page.tsx` - Dashboard
- `app/src/app/projects/new/page.tsx` - Create project
- `app/src/app/projects/[id]/page.tsx` - Project detail + upload
- `app/src/app/api/` - REST API routes
- `app/src/lib/mongodb.ts` - Database connection
- `app/src/lib/models.ts` - MongoDB schemas

### Backend (Python)
- `python-services/main.py` - FastAPI server (Phase 2 ready)
- `python-services/requirements.txt` - Dependencies

## Database

All data stored in MongoDB at `mongodb://localhost:27017/document-correction`

Collections:
- `projects` - Project metadata
- `documents` - Uploaded PDF/HTML references
- `jobs` - Processing jobs

## Troubleshooting

**MongoDB not running?**
```bash
brew services start mongodb-community
```

**Port already in use?**
```bash
# Change port in app/package.json or python-services/main.py
npm run dev -- -p 3001
python -m uvicorn main:app --port 8001
```

**Missing Cloudinary credentials?**
Upload will fail - fill in `.env` file with real credentials

## What's Next?

Phase 2 will implement:
- PDF analyzer (PyMuPDF)
- HTML analyzer (BeautifulSoup + Playwright)
- Image extraction & matching
- Comparison engine
- Auto-correction logic

Ready in **1 hour**! 🚀
