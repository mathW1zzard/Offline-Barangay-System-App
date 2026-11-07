Staff roles, step 9a (roles and permissions behind every page)
==============================================================

18 files. Drag everything in this folder except this note (the three .py files and the
"templates" and "static" folders) into your master-app folder and choose "Replace the files in the destination".

  New:      permissions.py, templates/no_access.html
  Replaced: database.py, main.py, static/style.css and 13 files in templates/

Before you start the app:
  1. Close the app.
  2. Keep a copy of barangay_system.db (in %LOCALAPPDATA%\Barangay_Queuing_Attendance_Management).
     The app also saves barangay_system.db.before_v8.bak by itself on the first start.
     An older build of the app can't open the database after this update.
  3. Don't copy these files into the Barangay 4 installation.

After the first start:
  - Your existing account is an Administrator and sees everything, same as before.
  - A "Secretary" role exists. There is no page for staff accounts yet; that is step 9b.
