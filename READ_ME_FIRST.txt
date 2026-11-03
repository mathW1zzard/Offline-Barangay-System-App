ABOUT PAGE UPDATE - FILES TO REPLACE
====================================

Made from your latest project (commit 772e7be, with the light / dark mode). These 17 files are
only today's changes: the new About page, Jo-Tech-Developer in every footer and in the installer,
the revised terms, and the open-source license list. Every other file in your project stays as it is.

BEFORE YOU COPY
  1. Your project has no uncommitted work right now except line endings, so you are ready.
     (If you change something before copying, commit it first:  git add -A  then  git commit -m "...")
  2. Close the app.

COPY
  3. Select everything inside this folder (not the folder itself), including .gitignore,
     drag it onto your project folder, and choose "Replace the files in the destination".
     Delete this READ_ME_FIRST.txt from the project afterwards.

CHECK
  4. Run  git status : it should list 15 changed files, and make_notices.py and
     static/fonts/OFL.txt as new files.
  5. Run  python make_notices.py  once in your virtual environment. It writes the license list the
     About page shows (build.bat does this on every build from now on).
  6. Start the app and open About, in light and in dark mode. The sidebar says
     "(c) 2026 Jo-Tech-Developer".

THE FILES
  New            make_notices.py, static/fonts/OFL.txt
  About page     main.py, templates/about.html
  Your name      templates/base.html, login.html, forgot_password.html, recovery_key_display.html,
                 setup1.html, setup2.html, wizard.html, installer.iss, README.md
  Terms          terms_and_conditions.txt
  Build          build.bat, Barangay_System.spec, .gitignore
