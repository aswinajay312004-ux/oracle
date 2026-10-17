# POPPYS AI operations runbook

This is for the person who looks after the POPPYS AI laptop. You do not need to be a programmer. Every task has the exact commands, what you should see, and what to do if you see something else.

Decisions behind this runbook: [RESEARCH_DECISIONS.md](RESEARCH_DECISIONS.md), "Logging and ops" and "Windows service and packaging", checklist items 45–47 and 49. First-time Oracle and app setup: [WINDOWS_SETUP.md](WINDOWS_SETUP.md).

> **Not yet tested on real Windows.** The service scripts in `deploy\windows` and the Windows parts of this runbook have been checked by tools only, never run on a Windows laptop. See [the list of what is untested](#not-yet-tested-on-real-windows). Rehearse every section on a spare Windows PC (or a virtual machine) before using it on the real laptop, and correct this runbook where reality differs.

## Quick facts

| What | Where or how much |
|---|---|
| The app in the browser | http://127.0.0.1:8008 |
| Windows service | **POPPYS AI** (service name `PoppysAI`), runs as LOCAL SERVICE, starts automatically about 2 minutes after Windows starts |
| Everything the service uses | `C:\ProgramData\PoppysAI` (below: *the install folder*) |
| Local data (users, drafts, templates, audit) | `C:\ProgramData\PoppysAI\data\poppys.sqlite3` |
| Backups | `C:\ProgramData\PoppysAI\data\backups`, every night at 01:30 |
| Logs | `C:\ProgramData\PoppysAI\logs` |
| Oracle settings (password) | `C:\ProgramData\PoppysAI\config\.env.poppys`, readable only by administrators and the service. Never in a backup |
| Orders saved to the ERP | Oracle (Docker container `oracle26ai`, FREEPDB1). **Not** covered by the POPPYS AI backup |
| Recovery targets | **RPO 24 h** (at most one day of local POPPYS AI work can be lost), **RTO 1 h** (back in service within one hour) |

How to open an administrator PowerShell: Start menu → type `PowerShell` → right-click **Windows PowerShell** → **Run as administrator**. Most commands below need it. If Windows refuses to run a script, first type:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

Many commands use these three shortcuts. Type them once in each new PowerShell window:

```powershell
$py   = 'C:\ProgramData\PoppysAI\current\.venv\Scripts\python.exe'
$bk   = 'C:\ProgramData\PoppysAI\current\scripts\backup.py'
$data = 'C:\ProgramData\PoppysAI\data'
```

(If your release ships its own Python, use `C:\ProgramData\PoppysAI\current\python\python.exe` for `$py`.)

---

## 1. Install

You do this once, after POPPYS AI already works with `start.ps1` (see WINDOWS_SETUP.md). *Not yet tested on real Windows.*

### Before you start

1. **Python for all users.** The service account cannot use a Python that was installed for one person only. Check the repository's `.venv\pyvenv.cfg`: the `home =` line must point to `C:\Program Files\Python313` (or similar), **not** `C:\Users\...` and not `WindowsApps`. If it points to `C:\Users\...`:
   1. Run the python.org installer for Python 3.13, choose **Customize installation**, tick **Install Python 3.13 for all users**.
   2. Delete the `.venv` folder in the repository.
   3. Run `.\setup.ps1` again.
2. **WinSW.** POPPYS AI uses WinSW 2.12.0 (MIT licence) to run as a service. It is not in the repository.
   1. Download `WinSW-x64.exe` from https://github.com/winsw/winsw/releases/tag/v2.12.0
   2. Rename it to `PoppysAI.exe`.
   3. Put it in the repository's `deploy\windows` folder.
   4. Check it: `Get-FileHash .\deploy\windows\PoppysAI.exe` must show
      `05B82D46AD331CC16BDC00DE5C6332C1EF818DF8CEEFCD49C726553209B3A0DA`.
      The install script checks this too and stops if it differs.
3. **Close the `start.ps1` window** (port 8008 must be free).

### Install the service

In an administrator PowerShell, in the repository folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\windows\install-service.ps1
```

You should see six numbered steps and, at the end, **Installed and running: POPPYS AI <version> at http://127.0.0.1:8008**. What it did:

- copied the app into `C:\ProgramData\PoppysAI\releases\<version>` and pointed `C:\ProgramData\PoppysAI\current` at it;
- copied your database, with its audit key and anchor (`audit.key`, `audit.anchor`: they prove the audit history was not changed), from the repository's `data` folder into `C:\ProgramData\PoppysAI\data` with a checked backup and restore. The original stays where it was, and a copy of that backup is in `data\backups\first-install`;
- copied `.env.poppys` into `C:\ProgramData\PoppysAI\config`;
- locked down the folders (only administrators can change the program; the service can write only to `data` and `logs`);
- registered and started the service.

Then register the nightly backup (section 4) and run it once:

```powershell
.\deploy\windows\backup-task.ps1 -RunNow
```

Open http://127.0.0.1:8008 and sign in with your usual account; your users, drafts and templates are there. From now on, **do not use `start.ps1`** on this laptop: the service already uses port 8008, and the repository's `data` folder is no longer used.

Running `install-service.ps1` again is safe. It repairs the installation: permissions, settings copy, service registration. It never copies files over the version that is running: if the repository has changed files under the same version name, it stops and says so. To move to a new version, use `upgrade.ps1` (section 6), not the install script.

If both the repository's `data` folder and `C:\ProgramData\PoppysAI\data` hold a database and the repository's one was changed later (you used `start.ps1` after removing the service, section 8), the script stops before changing anything, shows what each database holds and asks you to choose. Run it again with one of:

- `-UseDatabase Source`: use the repository's database. The service's current database is moved aside (`poppys.sqlite3.before-restore-<time>`), not deleted.
- `-UseDatabase Installed`: keep the service's database. The repository's one is not used.

---

## 2. Start and stop

| To | Do this |
|---|---|
| See if it runs | `Get-Service PoppysAI` (Status `Running`), or open http://127.0.0.1:8008 |
| Stop | `Stop-Service PoppysAI` (waits up to 30 seconds for work in progress to finish) |
| Start | `Start-Service PoppysAI` |
| Restart | `Restart-Service PoppysAI` |
| Without PowerShell | Start menu → **Services** → **POPPYS AI** → Start / Stop / Restart |

- It starts by itself about 2 minutes after Windows starts ("Automatic (Delayed Start)").
- If it crashes, Windows restarts it after 10 seconds, then 30 seconds, then every 60 seconds. After one hour without a crash the count starts again.
- Uploading and preparing orders works without Oracle. **Saving to Oracle needs Docker Desktop and the `oracle26ai` container** (section 9.1).

*Not yet tested on real Windows:* the restart-after-crash timing and the 30-second stop.

---

## 3. Logs

| File (in `C:\ProgramData\PoppysAI\logs`) | What is in it |
|---|---|
| `PoppysAI.err.log` | Start-up messages and errors of the app (Python error details). **Look here first.** |
| `PoppysAI.out.log` | Other app output |
| `PoppysAI.wrapper.log` | The service wrapper (WinSW): when the service started, stopped or was restarted |
| `deploy-history.log` | Every install, upgrade and rollback, with date and result |
| `failed-upgrade-<version>-<time>.err.log` | The error log of an upgrade that was rolled back |

Also:

- `C:\ProgramData\PoppysAI\data\backups\backup.log`: one line per backup, check, restore and drill (`BACKUP OK`, `BACKUP FAILED ...`, `DRILL OK ...`).
- Windows Event Viewer → **Windows Logs → System**: "Service Control Manager" messages when the service stops unexpectedly.

To watch the error log live (Ctrl+C to stop watching):

```powershell
Get-Content C:\ProgramData\PoppysAI\logs\PoppysAI.err.log -Tail 50 -Wait
```

Each log file is rolled over at 10 MB and the last 8 are kept, so logs cannot fill the disk. Ordinary users can read the logs; only administrators can read `data` and `config`.

---

## 4. Daily backup

### What happens every night

At **01:30** Task Scheduler runs `C:\ProgramData\PoppysAI\nightly-backup.cmd` as LOCAL SERVICE (task `\POPPYS AI\Nightly backup`). That small file, written by `backup-task.ps1`, starts `scripts\backup.py` with the Python of the version that is active at that moment, so the task keeps working after an upgrade. If the laptop was off at 01:30, it runs as soon as the laptop is on again. The backup:

1. copies the database with SQLite's online backup function, in one go, so the app can keep running (a plain file copy could catch a half-written file). While the copy runs, a save in the app waits a moment;
2. checks the copy: `integrity_check` must say `ok`, and `foreign_key_check` must find nothing;
3. writes `poppys-backup-<date>-<time>.zip` containing the database, the audit key and anchor (`data\audit.key`, `data\audit.anchor`), `config\oracle_mapping.json`, `config\semantic_catalog.json`, `data\schema_discovery.json` and a manifest with the SHA-256 of every file. Next to it, it writes `<name>.zip.sha256`;
4. opens the finished zip and checks everything again;
5. deletes old backups, keeping the newest backup of each of the last **14 days**, **8 weeks** and **12 months**. It deletes only after a new backup has passed its checks, so a failing backup never eats the good ones. Backups in `data\backups\pre-upgrade` and `data\backups\first-install` are never deleted automatically.

Never in a backup: `.env.poppys` (the Oracle password) or any other secret. The audit key is not a password: it only proves that nobody rewrote the audit history, and a restore needs it. Keep a copy of `.env.poppys` somewhere safe and separate, for example an encrypted USB stick in a locked drawer. Without it, a rebuilt laptop needs a new Oracle password (section 10).

**The Oracle database is not in this backup.** The orders saved to the ERP live in Oracle. On this laptop Oracle Free in Docker is a rehearsal system. The production Oracle server must have its own backups, made by the DBA (or Agile Labs).

### Register the task (once, and again after changing options)

```powershell
C:\ProgramData\PoppysAI\current\deploy\windows\backup-task.ps1 -RunNow
```

Options: `-At 02:15` (another time), `-SecondCopy D:\PoppysBackups` (also copy every backup to a second drive; a drive root such as `E:\` works too), `-IncludeUploads` (also back up the uploaded buyer files), `-Remove` (unregister; backups are kept). Without the service (only `start.ps1`), add `-AppRoot C:\path\to\oracle`. Every upgrade registers the task again with the same time and options (`backup-task.ps1 -Refresh`).

**Second location.** Keep a copy away from the laptop's disk: `-SecondCopy` to a second internal drive or an always-connected USB disk, or once a week copy the newest zip and its `.sha256` file to an encrypted USB stick (BitLocker To Go), and keep the stick in another room. The backups contain order details and password hashes: treat them as confidential.

### Check that it worked (once a week, 2 minutes)

1. Open `C:\ProgramData\PoppysAI\data\backups\backup.log`. The last line should be from last night and say `BACKUP OK`.
2. Or in PowerShell: `Get-ScheduledTaskInfo -TaskPath '\POPPYS AI\' -TaskName 'Nightly backup'`. `LastTaskResult` must be `0`.
3. Check the newest backup file end to end:

   ```powershell
   & $py $bk --data-dir $data --verify latest
   ```

   Expected: six `ok` lines (the last one: `audit key and anchor ... ok`) and **OK: the backup is complete and readable.** A line `WARNING: this backup has no audit key` means the backup was made by an older version: the data is complete, but after restoring it the audit history cannot be proven untouched. Make a new backup now (below).

| Result / exit code | Meaning | What to do |
|---|---|---|
| 0 | OK | Nothing |
| 1 | FAILED: the backup or a check failed | Read the message (also in `backup.log`). For "damaged database", see section 9.4. For "disk is too full", see section 9.2 |
| 2 | Set-up problem (wrong folder, missing file) | Run `backup-task.ps1` again; read the message |
| 3 | REFUSED (only for restores) | See section 5 |
| 4 | Backup fine, but the copy to the second location failed | Connect the second drive; the next night copies again |
| 5 | Restored, but the audit history of the restored data cannot be proven untouched (only for restores) | Read the warning. Usually the backup had no `audit.key`. The data is restored; tell support, and keep the output |

To make a backup **now** (before maintenance, or after a big day): `& $py $bk --data-dir $data`. To list the backups and see which ones retention keeps: `& $py $bk --data-dir $data --list`.

---

## 5. Monthly restore drill

A backup is only proven when it has been restored. Do this on the first working day of every month. It takes about 10 minutes. The live app keeps running: the drill uses a scratch copy, on port 8018, with Oracle switched off. *Not yet tested on real Windows.*

1. Open an administrator PowerShell and type the three shortcuts (`$py`, `$bk`, `$data`) from the top of this runbook.
2. Note the time on a clock (for the 1-hour recovery target).
3. Run:

   ```powershell
   & $py $bk --data-dir $data --drill latest --start-app
   ```

4. You should see, in this order:
   - five `ok` lines (the backup file is complete);
   - `Restored into the scratch folder ...`;
   - `Restored database: integrity_check ok, foreign keys ok, row counts match`;
   - `Audit history of the restored data: ok (... entries, keyed chain and anchor match)`;
   - `Starting the restored app on http://127.0.0.1:8018 ...`;
   - `The restored app answered ... with 200.` and `The restored app reads its users from the restored database (needs_setup=False)`;
   - **OK: restore drill passed for poppys-backup-....zip**
5. Record it. The drill writes `DRILL OK <backup name> app-started` into `data\backups\backup.log`. Also write the date, the backup name, the result, how long it took and your name in the site's maintenance log.
6. Every three months, also check the backups on the second location the same way. Add `--dest D:\PoppysBackups` (your second folder) to the command in step 3.

Optional, to look at the restored data yourself: add `--keep-scratch` to step 3. The drill then prints the scratch folder it kept. Then:

```powershell
cd <the scratch folder>\app
& $py -E -s -m uvicorn backend.main:app --host 127.0.0.1 --port 8018 --no-proxy-headers
```

Open http://127.0.0.1:8018 and sign in with your normal account. Check that **Setup → Audit history** and the drafts show yesterday's work. Saving and **Saved orders** do not work here, because Oracle is deliberately switched off. Press Ctrl+C in PowerShell, then delete the scratch folder.

**If the drill fails:** the live system is not affected. Run `& $py $bk --data-dir $data --verify <path of an older backup>` on the previous two backups. If those pass, the newest file is damaged: check the disk (section 9.4, step 6) and watch the next night's backup. If the drill says the restored audit history did not pass the check, the backup's audit key is missing or does not match: make a new backup now and drill that one. Keep the failing zip and the drill output for support.

### Restore for real

Use this when the live database is lost or damaged, or support asks you to go back to a backup. Anything entered in POPPYS AI after the backup was made is lost; orders already saved to Oracle stay in Oracle. *Not yet tested on real Windows.*

1. Choose the backup: `& $py $bk --data-dir $data --list`. Normally the newest one; for a failed upgrade, the one in `data\backups\pre-upgrade`.
2. Find the active version: `C:\ProgramData\PoppysAI\current\deploy\windows\rollback.ps1 -List` (the line marked `<- active`).
3. Restore it. This stops the service (with restart-after-crash paused, so Windows cannot start it in the middle), restores, starts it again and waits until it answers:

   ```powershell
   C:\ProgramData\PoppysAI\current\deploy\windows\rollback.ps1 -To <active version> -RestoreDatabase "<full path of the zip>"
   ```

   The current database, its audit key and anchor are **moved aside, never deleted**: `poppys.sqlite3.before-restore-<time>` and so on. If anything goes wrong half-way, every file is put back and the service starts again on the old data. The restore refuses (`REFUSED`) a backup made by a newer POPPYS AI version than the active one, because that version would refuse to start on it.
4. Open the app, sign in, and check the newest drafts and **Setup → Audit history**.
5. Re-upload the buyer files of any work done after the backup time. The duplicate checks stop an order that is already in Oracle from being saved twice ("This exact workbook has already been committed").

Without the service (only `start.ps1`): close the `start.ps1` window, then `.\.venv\Scripts\python.exe scripts\backup.py --restore <zip> --to .\data --force` in the repository folder, then `.\start.ps1`. The script refuses (`REFUSED`, exit code 3) while POPPYS AI is still running or another program has the database open.

On a **new or rebuilt laptop**: set up Docker/Oracle and Python as in WINDOWS_SETUP.md, install the service from the release folder (section 1; without an old database it starts empty), put your safe copy of `.env.poppys` into `C:\ProgramData\PoppysAI\config`, run `install-service.ps1` again, then restore the newest backup from the second location with the steps above, giving `--restore` the full path of the zip on that drive.

---

## 6. Upgrade

*Not yet tested on real Windows, including the automatic rollback.*

1. Get the new release folder:
   - from a release zip: unpack it to, for example, `C:\Temp\poppys-ai-1.3.0`, then unblock the files:
     `Get-ChildItem -Recurse C:\Temp\poppys-ai-1.3.0 | Unblock-File`;
   - or from git: `git pull` in the repository, then `.\setup.ps1` (it updates `.venv`). From git, every commit is its own version: the `VERSION` file plus the short commit, for example `0.9.0-abc1234` (and `-dirty-<hash>` when files were changed without a commit). So an upgrade after `git pull` always installs the new code, even when `VERSION` did not change.
2. In an administrator PowerShell, run the **new** release's upgrade script:

   ```powershell
   C:\Temp\poppys-ai-1.3.0\deploy\windows\upgrade.ps1 -Source C:\Temp\poppys-ai-1.3.0
   ```

3. What it does, in order:
   1. checks the release (`SHA256SUMS` when the release has one) and copies it into `releases\<version>`, while the old version keeps running;
   2. makes a checked backup into `data\backups\pre-upgrade` (database, audit key and anchor). It gets 30 minutes (`-BackupMinutes` to change that). If it fails or takes longer, the upgrade stops and nothing has changed. Pre-upgrade backups are never deleted automatically, so a second attempt on the same day keeps the first backup too;
   3. stops the service, points `current` at the new version, starts it, and waits up to 60 seconds for it to answer (`/readyz`, or the home page and `/api/session` in versions without `/readyz`);
   4. **if the new version does not answer, it rolls back by itself** to the version that was running, and keeps the failed start's error log in `logs\failed-upgrade-...err.log`. If the new version had already upgraded the database (a newer database version that the old version cannot open), the rollback also puts the pre-upgrade backup back; nothing was entered in the meantime, because the new version never answered;
   5. after a successful upgrade, registers the nightly backup task again with the same time and options.
4. Results:
   - exit code 0 and **Upgrade finished**: done. Open the app and check that it works.
   - exit code 1: the upgrade failed. Either it stopped before changing anything, or it rolled back and the old version runs again. Send the `logs` folder to support.
   - exit code 2: the upgrade **and** the automatic rollback failed. POPPYS AI is down. Go to [If the rollback does not start either](#if-the-rollback-does-not-start-either).

Old versions stay in `C:\ProgramData\PoppysAI\releases` (each is a few hundred MB). To free space, delete versions older than the previous one. **Remove the data link first**, otherwise you could delete the live data:

```powershell
cmd /c rmdir "C:\ProgramData\PoppysAI\releases\<old version>\data"
cmd /c rmdir /s /q "C:\ProgramData\PoppysAI\releases\<old version>"
```

Never delete the version that `rollback.ps1 -List` marks active or previous.

Pre-upgrade backups stay in `data\backups\pre-upgrade` until you delete them. Keep at least the ones of the active and the previous version; older ones can be moved to the second location or deleted (with their `.sha256` file).

---

## 7. Rollback

Use this when a new version passed the upgrade check but misbehaves later. *Not yet tested on real Windows.*

```powershell
cd C:\ProgramData\PoppysAI\current\deploy\windows
.\rollback.ps1 -List                # shows the versions, the active one and the previous one
.\rollback.ps1                      # back to the previous version
.\rollback.ps1 -To <version>        # or to a version from the list
```

It pauses restart-after-crash, stops the service, checks that the older version can open the database, points `current` at that version, starts it and waits up to 60 seconds for it to answer. The database itself is kept as it is when the older version can open it.

**When the newer version upgraded the database.** Some versions change how the database is laid out (a newer database version, numbered inside the file). An older version refuses to start on such a database. `rollback.ps1` checks this before it stops anything; if so, it changes nothing and prints the exact command to go back **with** the backup made before the upgrade. That command loses everything entered in POPPYS AI since the upgrade (orders already saved to Oracle stay in Oracle):

```powershell
.\rollback.ps1 -To <old version> -RestoreDatabase "C:\ProgramData\PoppysAI\data\backups\pre-upgrade\poppys-backup-<date>-<time>-pre-upgrade-<old version>.zip"
```

Talk to support before you run it: the problem in the new version can often be fixed without losing that work. The automatic rollback during an upgrade (section 6) restores this backup by itself, because nothing was entered in between.

### If the rollback does not start either

1. Read the last lines of `C:\ProgramData\PoppysAI\logs\PoppysAI.err.log`. The most common causes are Python problems after a Windows or Python update, a full disk (section 9.2) or another program on port 8008.
2. Try the version before, with the pre-upgrade database. The upgrade message printed the exact command, with the full path of `rollback.ps1` (`& 'C:\ProgramData\PoppysAI\releases\<new version>\deploy\windows\rollback.ps1' -To ... -RestoreDatabase ...`).
3. Still down after 30 minutes: use the app without the service until support helps ([Going back to start.ps1](#going-back-to-startps1)). That keeps you within the 1-hour recovery target.

---

## 8. Uninstall

```powershell
C:\ProgramData\PoppysAI\current\deploy\windows\uninstall-service.ps1                     # service only
C:\ProgramData\PoppysAI\current\deploy\windows\uninstall-service.ps1 -RemoveBackupTask   # and the nightly backup
```

Nothing is deleted: data, backups, logs, settings and releases stay in `C:\ProgramData\PoppysAI`. Delete that folder by hand only after copying `data\backups` somewhere safe.

### Going back to start.ps1

1. `uninstall-service.ps1` (above).
2. Put the newest data into the repository folder:

   ```powershell
   cd C:\path\to\oracle
   .\.venv\Scripts\python.exe scripts\backup.py --dest C:\ProgramData\PoppysAI\data\backups --restore latest --to .\data --force
   ```

   (the older database in `.\data`, with its audit key and anchor, is moved aside, not deleted).
3. If `.env.poppys` is no longer in the repository folder, copy it back from `C:\ProgramData\PoppysAI\config`.
4. `.\start.ps1`. For nightly backups in this mode: `.\deploy\windows\backup-task.ps1 -AppRoot C:\path\to\oracle`.

To go back to the service later, run `install-service.ps1` from the repository (section 1). Because the repository's database is now the newer one, it asks which database to use: answer with `-UseDatabase Source` to take the work done with `start.ps1` into the service.

---

## 9. When something goes wrong

### 9.1 Oracle or Docker is down

**Signs:** the status light shows Oracle offline; approving says Oracle is not reachable; Ask AI says "Oracle is not connected". Uploading and preparing previews still work.

1. Is Docker Desktop running (whale icon near the clock)? If not, start it and wait until it says "Engine running".
2. In PowerShell: `docker ps --filter name=oracle26ai`. If the container is not listed as `Up`, run `docker start oracle26ai`.
3. Wait until `docker ps` shows `(healthy)` (1–3 minutes), then refresh the browser. You do not need to restart POPPYS AI.
4. Still failing: `docker logs --tail 50 oracle26ai`. A common cause is a full disk (section 9.2).
5. **Never** delete the container or its volumes (`docker rm`, `docker volume rm`, `docker system prune --volumes`). That deletes the database.

To make Oracle come back by itself after a restart:

- Docker Desktop → Settings → General → tick **Start Docker Desktop when you sign in to your computer**.
- Run once: `docker update --restart unless-stopped oracle26ai`.

Docker Desktop only starts after someone signs in to Windows. Until then, POPPYS AI runs but cannot save to Oracle.

### 9.2 The disk is full

**Signs:** the backup log says "The disk is too full"; the app says "database or disk is full"; Oracle stops or reports ORA-01653 / ORA-01688; Docker says "no space left on device".

1. Check free space: `Get-PSDrive C` (the `Free` column) or File Explorer → This PC.
2. Free space safely, in this order:
   1. Windows **Disk Cleanup** → **Clean up system files** (Windows Update leftovers, temporary files).
   2. Your own Downloads folder and the recycle bin.
   3. Old POPPYS AI versions (section 6, "To free space").
   4. Unused Docker images: `docker image prune`. This deletes only images no container uses. Never prune volumes.
   5. Old monthly backups. Move them to the second location rather than deleting them, and never touch the newest ones.
3. Logs are capped (10 MB × 8 per file) and are not the cause.
4. When there is space again: make a backup now and verify it (section 4). If Oracle had stopped, start it again (section 9.1).

Keep at least 5 GB free. `/readyz` (when the app version has it) warns below 1 GB.

### 9.3 A save failed

**Nothing partial is saved.** Saving an approved order to Oracle is one transaction: all order tables and the receipt row in `PP_IMPORT_LOG` are written together, or, on any error, all of it is undone. The error message says "Nothing was partially saved".

Where to look:

1. The message on screen. It names the problem (Oracle not reachable, preview out of date, duplicate order reference, a value too long ...).
2. **Setup → Audit history** (administrators and approvers). A failed save is an `approve_failed` event with the error text, and a successful one is `approved_saved`. The target column is the import's ID.
3. `PP_IMPORT_LOG` in Oracle, the receipt written in the same transaction. In SQL Developer:

   ```sql
   SELECT JOBID, APPROVER, COMMITTED_AT FROM POPPYS_APP.PP_IMPORT_LOG ORDER BY COMMITTED_AT DESC;
   ```

   - **No row for that import:** it was not saved. Fix the cause (for example start Oracle, section 9.1), prepare the preview again, and approve again.
   - **A row exists:** it was saved. For example, the connection broke just after the commit. Approving again is safe: POPPYS AI looks for the receipt first and shows the saved result, and it never saves the same order twice.
4. Technical details: `C:\ProgramData\PoppysAI\logs\PoppysAI.err.log` at that time.

### 9.4 A backup reports a damaged database

**Signs:** `backup.log` or the task result says `FAILED: The database copy failed PRAGMA integrity_check ...` or "database disk image is malformed". The older backups are kept, because nothing is pruned after a failure.

1. `Stop-Service PoppysAI`
2. Keep the evidence: copy the whole data folder (not only the database) to another drive, for example
   `robocopy C:\ProgramData\PoppysAI\data D:\PoppysCase\data /E`.
3. Find the newest good backup: `& $py $bk --data-dir $data --verify <zip>`, starting with the newest zip and moving to older ones until one says **OK**.
4. Restore it (section 5, "Restore for real", step 3).
5. `Start-Service PoppysAI`. Re-enter or re-upload what was done after that backup.
6. Check the disk: `chkdsk C: /scan` (administrator), and look at **Windows Security → Device performance & health**. Repeated damage means the disk is failing: replace it and restore onto the new one.
7. Send support `data\backups\last-failed-check.sqlite3` (the copy that failed the check), `backup.log` and the logs folder.

### 9.5 The service does not start or keeps restarting

1. `Get-Content C:\ProgramData\PoppysAI\logs\PoppysAI.err.log -Tail 50` shows the reason.
2. "Address already in use" or port 8008 busy: close a `start.ps1` window or end the old `python` process in Task Manager.
3. Python errors after a Windows or Python update: run `install-service.ps1` again from `C:\ProgramData\PoppysAI\current\deploy\windows` (it checks that Python can load the app and says what is wrong).
4. Right after an upgrade: `rollback.ps1` (section 7).

---

## 10. Rotate the Oracle password

The app signs in to Oracle as `POPPYS_APP`. Change its password when someone who knew it leaves, when it may have been seen, and at least once a year. The new password is never shown on screen and never typed into a file by hand. *Not yet tested on real Windows.*

In an administrator PowerShell (Docker and `oracle26ai` running):

```powershell
# 1. Stop the app so no save is in progress.
Stop-Service PoppysAI

# 2. Make a new random password; it exists only in this window.
$bytes = New-Object byte[] 24
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$new = 'Pp' + (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')

# 3. Change it in Oracle. It is sent through a pipe and SQL*Plus's output is thrown away, so the password appears
#    nowhere: not on screen (SQL*Plus repeats a failing statement in its error message), not in the process list.
$sql = "whenever sqlerror exit failure`nwhenever oserror exit failure`nalter session set container=FREEPDB1;`nalter user POPPYS_APP identified by `"$new`";`nexit"
$sql | docker exec -i oracle26ai sqlplus -s / as sysdba | Out-Null
$ok = ($LASTEXITCODE -eq 0)
Remove-Variable sql
if ($ok) { Write-Host 'OK: Oracle has the new password.' -ForegroundColor Green } else { Write-Host 'FAILED: Oracle did not take the new password. Nothing was changed: run Start-Service PoppysAI and read below.' -ForegroundColor Red }

# 4. Only if step 3 said OK: put it into the settings file (the other lines stay as they are).
$file = 'C:\ProgramData\PoppysAI\config\.env.poppys'
if ($ok) {
  $keep = Get-Content $file | Where-Object { $_ -notmatch '^ORACLE_PASSWORD=' }
  Set-Content $file -Value ($keep + "ORACLE_PASSWORD=$new") -Encoding ASCII
}
Remove-Variable new, bytes

# 5. Copy the settings into the running version (with the right permissions) and start the service.
if ($ok) { C:\ProgramData\PoppysAI\current\deploy\windows\install-service.ps1 }
```

Then check: open the app and confirm the Oracle status light is green, then prepare a preview. Close the PowerShell window. Update your safe offline copy of `.env.poppys` (section 4). If an old copy of `.env.poppys` is still in the repository folder, delete it or update it the same way.

If step 3 says FAILED: check that Docker and `oracle26ai` run (section 9.1), then start again at step 2 (the old password still works, so start the service first). If the app cannot connect after step 5, the file and Oracle do not match. Repeat steps 1–5.

---

## 11. Recovery targets: RPO 24 h, RTO 1 h

- **RPO (how much work can be lost): 24 hours.** The backup runs every night, so at worst the POPPYS AI work since the last 01:30 backup is lost: drafts, edits, templates, users, audit history. Orders already saved to Oracle are not affected. To lose less, back up by hand after a big day and before maintenance; upgrades always back up first.
- **RTO (how long until it works again): 1 hour.** A restore takes minutes (section 5, "Restore for real"). The hour also covers finding the cause, checking, and re-uploading the files of the lost day. The monthly drill proves the restore steps; time it. On a new laptop, the hour assumes that Python, Docker/Oracle, the release zip, a backup on the second location and a safe copy of `.env.poppys` are at hand. Without the second-location copy, a dead disk means everything since the last copy is gone.

---

## Not yet tested on real Windows

Everything below was written and checked on Linux only. Rehearse it on a clean Windows 10/11 virtual machine before go-live (checklist items 46 and 49), and record the results here.

What was checked:

- `scripts\backup.py` passes its unit tests (`tests\test_backup.py`: corrupted zips are detected, retention keeps the right files, a restore equals the source rows, a restore that fails half-way puts every file back, a backup from a newer database version is refused, the audit key and anchor travel with the database so the audit history still verifies after a restore, a backup finishes while the app writes every few milliseconds, the drill starts a restored app).
- Functions of the `.ps1` scripts were run on their own under PowerShell 7 on Linux: the command-line quoting of the nightly task (`E:\`), the version name of a git checkout (`0.9.0-abc1234`), the docs copy (only files git tracks), the restart-after-crash settings read from `PoppysAI.xml`, the nightly task's `.cmd` file. `setup.ps1` was run with stand-in programs: it stops at the first failing step.
- The tests also run on the Windows CI job (Windows PowerShell 5.1, through `scripts\check.ps1`), including a parse of every `deploy\windows\*.ps1` file, once they are pushed.
- The `.ps1` scripts were parsed with PowerShell 7.4 and analysed with PSScriptAnalyzer 1.23 against the Windows PowerShell 5.1 command set. Their checksum, version, port and readiness functions were exercised under PowerShell 7 on Linux.
- `PoppysAI.xml` is well-formed and uses the WinSW 2.12.0 element names from its v2.12.0 documentation.

Not yet run on Windows:

1. Every `.ps1` script end to end: install, upgrade, automatic rollback after a forced `/readyz` failure, manual rollback, uninstall.
2. WinSW: the service as LOCAL SERVICE, delayed start, restart within 10 seconds after `python.exe` is killed, stop in under 30 seconds through Ctrl+C, log rolling, `%BASE%` in `env`, `logpath` and `workingdirectory`, the executable reached through the `current` junction.
3. The `.venv` launcher with `-E -s -X utf8` under LOCAL SERVICE (the install script checks the flags as an administrator, not as the service account).
4. The `icacls` permissions with well-known SIDs: the service can write only to `data` and `logs`, and ordinary users cannot read `data` or `config`.
5. Task Scheduler: a task registered for `S-1-5-19` with LogonType ServiceAccount; "run as soon as possible after a missed start"; the S4U task without the service.
6. `backup.py` on Windows: replacing files, refusing while the database is open, and the drill's stop of the restored app through the `.venv` launcher.
7. Section 10 (password rotation, including that `whenever sqlerror exit failure` makes `docker exec ... sqlplus` exit non-zero on Windows), sections 9.1 and 9.2 (Docker auto-start, `docker update --restart`), and the junction-safe release deletion in section 6.
8. Release packaging (embeddable Python 3.13.16, hashed wheelhouse, SHA256SUMS, Authenticode signing; checklist item 47) does not exist yet. The scripts already verify `SHA256SUMS` and use `python\python.exe` when a release ships them.
9. `rollback.ps1`: `sc.exe failure PoppysAI reset= 0 actions= ""` pauses restart-after-crash (also for a restart Windows had already scheduled before the pause?), and the settings from `PoppysAI.xml` come back afterwards (`sc.exe qfailure PoppysAI`); the database check and the automatic restore after an upgrade whose new version changed the database; `sc.exe config PoppysAI start= demand` when a restore leaves no database.
10. `upgrade.ps1`: the backup time limit (`System.Diagnostics.Process` and `taskkill /T` stopping the `.venv` launcher and its child Python), and `backup-task.ps1 -Refresh` after the upgrade.
11. The nightly task running `nightly-backup.cmd` as LOCAL SERVICE (Task Scheduler starting a `.cmd` file with arguments, its exit code as `LastTaskResult`).
12. `install-service.ps1`: the version name from git run as an administrator on a checkout owned by another Windows user (git's "dubious ownership" check; the script then uses a hash of the files), the `-UseDatabase` question after section 8, the `.env.poppys` copy staged in `config\` and moved into the release with its permissions kept (`icacls <release>\.env.poppys` must show no Users entry).
13. `setup.ps1` with the real `py`, pip, Docker and Oracle (only stand-ins were used).

---

## Appendix

### Exit codes

| Script | 0 | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|---|
| `scripts\backup.py` | OK | FAILED | set-up problem | REFUSED (restore; `--db-info`: the database is newer than that version) | second copy failed | restored, audit history unproven |
| `install-service.ps1` | OK | failed | – | (`-StageOnly`) already active | – | – |
| `upgrade.ps1` | upgraded | failed (stopped early or rolled back) | upgrade and rollback failed | – | – | – |
| `rollback.ps1` | OK | failed (also: the database is too new for that version; nothing changed) | – | nothing to do | – | – |
| `backup-task.ps1`, `uninstall-service.ps1` | OK | failed | – | – | – | – |

### The install folder

```
C:\ProgramData\PoppysAI\
  PoppysAI.exe, PoppysAI.xml    the service wrapper (WinSW 2.12.0) and its settings (copied from the release)
  nightly-backup.cmd            what the nightly backup task runs (written by backup-task.ps1)
  current\                      link to the active version in releases\
  previous.txt                  the version before the last upgrade (default for rollback.ps1)
  releases\<version>\           one folder per installed version (its data\ is a link to ..\..\data)
  data\                         poppys.sqlite3, audit.key and audit.anchor (they belong with the database),
                                uploaded files, backups\ (backups\pre-upgrade and backups\first-install are never pruned)
  logs\                         service logs, deploy-history.log
  config\.env.poppys            Oracle settings (administrators and the service only)
```

WinSW-x64.exe 2.12.0 SHA-256: `05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da` (calculated from https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe on 2026-10-17).
