#  Face Recognition-Based Attendance System

A real-time face recognition-based attendance system built using **Python**, **Django**, **OpenCV**, and **FaceNet-PyTorch**. This system captures student faces using a webcam, performs deep learning-based recognition, and marks attendance automatically in a contactless and secure manner.

---

## 📌 Key Features

- 🔐 Admin login with secure dashboard access
- 👨‍🎓 Student registration, authorization, and management
- 📸 Real-time facial recognition and attendance marking
- 📊 Detailed attendance reports and logs
- 🖥️ Bootstrap-powered responsive user interface
- 📂 SQLite backend for efficient data storage

---

## 🛠️ Technologies Used

| Technology      | Purpose                                |
|-----------------|----------------------------------------|
| Python 3.10+    | Core programming language              |
| Django          | Web framework                          |
| OpenCV          | Real-time video feed and image processing |
| FaceNet-PyTorch | Deep learning-based face recognition   |
| SQLite          | Lightweight database                   |
| Bootstrap       | Frontend design and responsiveness     |

---

## ⚙️ Setup Instructions

### 1. Clone the Repository
```bash
git clone https://github.com/RajeebLochan/Project-Face-attandence-system.git
cd Project-Face-attandence-system
```
2. Create and Activate Virtual Environment
```bash
python -m venv venv

# For Linux/macOS
source venv/bin/activate

# For Windows
venv\Scripts\activate
```
3. Install Required Dependencies
```bash
pip install -r requirements.txt
```
4. Run the Django Server
```bash
python manage.py runserver
```
⚠️ Note for Windows Users: You must have Visual C++ Build Tools installed to compile some dependencies.
💡 Linux Users: No additional C++ setup is required.


## Administrator account

No credentials are shipped with this project. Create your own administrator after
running migrations:

```bash
python manage.py migrate
python manage.py createsuperuser
```

> Security notice: earlier versions of this repository published an administrator
> password, the Django secret key and personal data. See
> [`docs/INCIDENT_CLEANUP_RUNBOOK.md`](docs/INCIDENT_CLEANUP_RUNBOOK.md).
