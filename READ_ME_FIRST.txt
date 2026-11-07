Staff accounts, step 9b (includes all of step 9a)
================================================

26 files. Drag everything in this folder except this note (the three .py files and the
"templates" and "static" folders) into your master-app folder and choose
"Replace the files in the destination".

  New:      permissions.py and 6 pages in templates/ (no_access, staff, staff_password,
            roles, role_form, first_login)
  Replaced: database.py, main.py, static/app.js, static/style.css and 16 pages in templates/

If you already applied the 9a folder, just replace again: these files include 9a.

Before you start the app:
  1. Close the app.
  2. Keep a copy of barangay_system.db (in %LOCALAPPDATA%\Barangay_Queuing_Attendance_Management).
     The first start also saves barangay_system.db.before_v8.bak by itself (only if it isn't
     version 8 yet). An older build of the app can't open the database after this update.
  3. Don't copy these files into the Barangay 4 installation.

What to try:
  1. Log in as usual. You are the Administrator. Setup > Staff accounts is new.
  2. Add staff account: type a name and a username, keep the Secretary role.
     Write down the temporary password the app shows.
  3. Log out, log in as that person: the app asks for a new password, then shows
     their own recovery key.
  4. Roles tab: add a role, tick a few boxes, give it to the person (Edit), and
     check what their sidebar shows.
