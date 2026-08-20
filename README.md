# Document Correction Platform - Phase 1 ✅

Complete Phase 1 implementation (2 hours) - Production-ready Next.js + MongoDB + Cloudinary

## 📋 What's Included

### Frontend (Next.js 15)
- Dashboard with project listing
- Create new projects
- Upload PDF + HTML files to Cloudinary
- Project detail view
- Job creation (link to Phase 2)
- Fully typed with TypeScript
- Tailwind CSS styling

### Backend (REST APIs)
- Project CRUD (`POST`, `GET`, `PUT`, `DELETE` /api/projects)
- Document management (`POST`, `GET` /api/documents)
- File upload to Cloudinary (`POST` /api/upload)
- Job management (`POST`, `GET` /api/jobs)
- MongoDB integration with Mongoose

### Database (MongoDB)
- Projects collection
- Documents collection
- Jobs collection
- All with proper indexing

### Python Services (Phase 2 Ready)
- FastAPI server skeleton
- Health check endpoint
- Process endpoint (ready for implementation)
- Requirements.txt with all Phase 2 dependencies

## 🚀 Quick Start (5 minutes)

### 1. Prerequisites
```bash
# macOS
brew install node mongodb-community

# Ubuntu/Linux
sudo apt-get install nodejs mongodb
```

### 2. Setup
```bash
cd document-correction-platform

# Copy environment file
cp .env.example .env

# Fill in Cloudinary credentials
# CLOUDINARY_CLOUD_NAME=your_name
# CLOUDINARY_API_KEY=your_key  
# CLOUDINARY_API_SECRET=your_secret

# Start MongoDB
brew services start mongodb-community

# Install dependencies
cd app
npm install
```

### 3. Run
```bash
cd app
npm run dev
```

Open **http://localhost:3000** ✨

## 📁 Project Structure

```
document-correction-platform/
├── app/                           # Next.js application
│   ├── src/
│   │   ├── app/
│   │   │   ├── api/              # REST API routes
│   │   │   ├── projects/         # Project pages
│   │   │   ├── page.tsx          # Dashboard
│   │   │   └── layout.tsx        # Root layout
│   │   ├── lib/
│   │   │   ├── mongodb.ts        # DB connection
│   │   │   ├── models.ts         # Mongoose schemas
│   │   │   └── cloudinary.ts     # File upload
│   │   └── app/globals.css       # Tailwind styles
│   ├── package.json
│   ├── tsconfig.json
│   ├── next.config.js
│   └── tailwind.config.ts
│
├── python-services/              # Python FastAPI (Phase 2)
│   ├── main.py                   # FastAPI server
│   └── requirements.txt          # Dependencies
│
├── .env.example                  # Environment template
├── PHASE1_QUICKSTART.md         # Quick start guide
├── README.md                     # This file
└── SETUP.sh                      # Automated setup
```

## 🛠️ Technology Stack

- **Frontend**: Next.js 15, React 19, TypeScript, Tailwind CSS
- **Backend**: Next.js API Routes, Mongoose ODM
- **Database**: MongoDB (local)
- **File Storage**: Cloudinary (cloud-based)
- **Python Services**: FastAPI, PyMuPDF, BeautifulSoup4 (Phase 2)

## 📡 API Endpoints

### Projects
```
POST   /api/projects              Create new project
GET    /api/projects              List all projects
GET    /api/projects/:id          Get single project
PUT    /api/projects/:id          Update project
DELETE /api/projects/:id          Delete project
```

### Documents
```
GET    /api/documents             List documents (filter by projectId)
POST   /api/documents             Create document reference
```

### Upload
```
POST   /api/upload                Upload file to Cloudinary
```

### Jobs
```
POST   /api/jobs                  Create processing job
GET    /api/jobs                  Get job status
```

## 💾 Database Schema

### Projects
```javascript
{
  _id: ObjectId,
  name: String,
  board: String,        // CBSE, ICSE, STATE_BOARD
  standard: Number,     // 8-12
  subject: String,      // Mathematics, etc.
  language: String,     // EN, ML, HI
  status: String,       // ACTIVE, ARCHIVED
  createdAt: Date,
  updatedAt: Date
}
```

### Documents
```javascript
{
  _id: ObjectId,
  projectId: ObjectId,
  type: String,         // PDF, HTML
  cloudinaryPublicId: String,
  cloudinaryUrl: String,
  mimeType: String,
  size: Number,
  checksum: String,     // SHA-256
  status: String,       // UPLOADED, PROCESSING, READY
  createdAt: Date,
  updatedAt: Date
}
```

### Jobs
```javascript
{
  _id: ObjectId,
  projectId: ObjectId,
  pdfDocumentId: ObjectId,
  htmlDocumentId: ObjectId,
  status: String,       // QUEUED, PROCESSING, COMPLETED, FAILED
  progress: Number,     // 0-100
  startedAt: Date,
  completedAt: Date,
  error: String,
  createdAt: Date
}
```

## 🎯 Phase 1 Features

✅ Create projects with metadata (board, standard, subject, language)
✅ Upload PDF and HTML files to Cloudinary
✅ Automatic checksum calculation (SHA-256)
✅ Full project management (CRUD)
✅ Document tracking
✅ Job creation (ready for Phase 2 processing)
✅ Responsive UI with Tailwind CSS
✅ Type-safe with TypeScript
✅ MongoDB persistence
✅ Error handling

## 📊 Testing Flow

1. **Create Project**
   - Click "New Project"
   - Enter: Name, Board, Standard (8-12), Subject, Language
   - Submit → Redirects to project detail

2. **Upload Files**
   - Select PDF file
   - Select HTML file
   - Click "Upload Files"
   - Files upload to Cloudinary
   - Document records created in MongoDB

3. **Start Processing**
   - Click "Start Processing" (appears after upload)
   - Job created in MongoDB
   - Ready for Phase 2 (Python services)

## 🔧 Troubleshooting

### MongoDB not connecting
```bash
# Start MongoDB
brew services start mongodb-community

# Test connection
mongosh
```

### Cloudinary upload failing
- Check `.env` has correct credentials
- Verify Cloudinary account is active

### Port already in use
```bash
# Change port in app/package.json
npm run dev -- -p 3001
```

### Node modules issues
```bash
rm -rf node_modules package-lock.json
npm install
```

## 📈 Phase 2 (Coming Soon)

Ready to implement when you start Phase 2:
- Python FastAPI server skeleton
- All dependencies listed in `requirements.txt`
- MongoDB connection ready
- Cloudinary integration ready

Start Phase 2 with:
```bash
cd python-services
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

## ✨ Key Implementation Details

### No Docker Required
- Everything runs locally
- MongoDB on local machine
- Next.js dev server
- Python dev server

### Production-Ready Code
- Proper error handling
- Type safety with TypeScript
- Modular structure
- Scalable architecture

### Database Connection
- Connection pooling in Next.js
- Mongoose for data validation
- Automatic indexing

### File Upload
- Client-side file selection
- Temporary file handling
- SHA-256 checksum verification
- Automatic cleanup

## 🚀 Ready to Go!

Everything is set up and ready to use. Just:
1. Fill in `.env` with Cloudinary credentials
2. Start MongoDB
3. Run `npm run dev` in the `app` directory
4. Visit http://localhost:3000

**Built in 2 hours. Production quality. Ready for Phase 2.** ✅

---

Need help? Check `PHASE1_QUICKSTART.md` for detailed instructions.
