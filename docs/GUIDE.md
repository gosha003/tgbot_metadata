# Guide: installing and using the bot

[Русская версия](GUIDE.ru.md)

This guide is for someone who has the link to the repository and wants to run
the bot on their own machine. No programming experience is needed: every
command can be copied as is. It takes about 15–20 minutes.

**Contents**

1. [What the bot does](#1-what-the-bot-does)
2. [How it works and what you need](#2-how-it-works-and-what-you-need)
3. [Installation](#3-installation) — [Windows](#windows) · [macOS](#macos) · [Linux](#linux)
4. [Your own Telegram bot (@BotFather)](#4-your-own-telegram-bot-botfather)
5. [The .env settings file](#5-the-env-settings-file)
6. [Starting and stopping](#6-starting-and-stopping)
7. [Optional: LibreOffice and exiftool](#7-optional-libreoffice-and-exiftool)
8. [How to use it](#8-how-to-use-it)
9. [Which profile to choose](#9-which-profile-to-choose)
10. [Reading the bot's replies](#10-reading-the-bots-replies)
11. [Limits](#11-limits)
12. [Privacy: where files go](#12-privacy-where-files-go)
13. [Common problems](#13-common-problems)
14. [Updating](#14-updating)
15. [Without Telegram: the command line](#15-without-telegram-the-command-line)

---

## 1. What the bot does

Besides the visible content, every document and every photo carries
**metadata**: hidden information about who made the file, where, when and with
what. For example:

- in a Word document: the author's name and the name of whoever saved it last,
  the organization, a folder path on the computer (`C:\Users\John\Documents\...`),
  the printer name, how many minutes the document was edited;
- in a PDF: the program that made it, dates, "earlier versions" of pages and
  the text under a "redaction" if the edits were appended on top;
- in a phone photo: **the GPS coordinates of where it was taken**, the camera
  model and serial number, the date and time.

The bot does two things:

1. **Shows** what's in the file (inspection). The file itself is not changed.
2. **Cleans**: sends back a new copy of the file without personal data. The
   original file is never changed.

The key feature: the bot tries to make sure **a cleaned file doesn't look
cleaned**. A file with every field empty is as suspicious as a file with all
the data in place. So by default the bot removes what's personal but keeps
what looks natural (for example, "made in Word" is simply true).

**Supported formats**

| Format | Show | Clean |
|---|---|---|
| Word / Excel / PowerPoint (`.docx`, `.xlsx`, `.pptx`) | yes | yes |
| PDF | yes | yes |
| Photos and images: JPEG, PNG, WebP, GIF | yes | yes |
| OpenDocument (`.odt`, `.ods`, `.odp`), RTF | yes | yes |
| Old Word / Excel / PowerPoint (`.doc`, `.xls`, `.ppt`) | yes | only by regenerating, needs LibreOffice ([section 7](#7-optional-libreoffice-and-exiftool)) |
| TIFF, HEIC (iPhone photos) | yes | no |
| A ZIP archive of documents | — | yes, all files inside at once |

---

## 2. How it works and what you need

The bot is a program that runs **on your computer**. Telegram only delivers
files to it and shows its replies. While the program runs, the bot answers.
Turn the computer off or close the window, and the bot goes quiet.

Everyone runs **their own** bot: each has its own "key" (token) from Telegram.
If two people run the program with the same token, the bot answers every other
time.

You need:

- a computer with Windows, macOS or Linux;
- **Python 3.11 or newer** (free, installation is described below);
- a Telegram account;
- about 200 MB of disk space.

---

## 3. Installation

### Windows

**Step 1. Python.**

1. Open [python.org/downloads](https://www.python.org/downloads/) and click
   the big yellow "Download Python 3.x" button.
2. Run the downloaded file. **Be sure to tick "Add python.exe to PATH"** at
   the bottom of the first window, then click "Install Now".
3. Check it: open the Start menu, type `cmd`, open "Command Prompt" and enter:

   ```
   python --version
   ```

   It should say `Python 3.11` or newer. If it says "is not recognized as an
   internal or external command", the PATH box wasn't ticked: reinstall Python
   with the box ticked.

**Step 2. Download the bot.**

The simplest way, without git: on the repository's GitHub page click the green
**Code → Download ZIP** button and unpack the archive, for example into
`C:\tgbot_metadata`.

If git is installed:

```
git clone https://github.com/gosha003/tgbot_metadata.git C:\tgbot_metadata
```

**Step 3. Install the dependencies.** In the Command Prompt:

```
cd C:\tgbot_metadata
python -m pip install -r requirements.txt
```

If the folder has a different name (after the ZIP it's often
`tgbot_metadata-main`), use your path. It should end with
`Successfully installed ...`.

Next: [section 4](#4-your-own-telegram-bot-botfather).

### macOS

**Step 1. Python.** Download the installer from
[python.org/downloads](https://www.python.org/downloads/) and install it. Then
open Terminal (Applications → Utilities → Terminal) and check:

```
python3 --version
```

On macOS the command is `python3`, not `python`.

**Step 2. Download the bot.** Code → Download ZIP on GitHub and unpack it, for
example into Documents. Or:

```
git clone https://github.com/gosha003/tgbot_metadata.git ~/tgbot_metadata
```

**Step 3. Dependencies in a virtual environment.** Recent macOS versions don't
allow installing packages into the system Python, so we create a separate
environment right in the bot's folder:

```
cd ~/tgbot_metadata
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The `source .venv/bin/activate` line has to be repeated **every time** before
starting the bot in a new terminal window (`(.venv)` appears at the start of
the line).

### Linux

For Ubuntu / Debian:

```
sudo apt update
sudo apt install python3 python3-venv python3-pip git
git clone https://github.com/gosha003/tgbot_metadata.git ~/tgbot_metadata
cd ~/tgbot_metadata
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

As on macOS, repeat `source .venv/bin/activate` before every start.

---

## 4. Your own Telegram bot (@BotFather)

1. In Telegram, find **@BotFather** (with the blue check mark) and press "Start".
2. Send it the `/newbot` command.
3. It asks for the bot's **name**: anything, for example `My cleaner`.
4. Then the **username**: unique, Latin letters, ending in `bot`, for example
   `john_cleaner_bot`. If it's taken, pick another one.
5. BotFather sends a message with the **token**, a string like
   `7123456789:AAHk3j...`. This is the key to the bot. **Never show it to
   anyone**: whoever knows the token controls the bot. If the token leaks, send
   BotFather `/revoke` and get a new one.

---

## 5. The .env settings file

The bot's folder has a sample file, `.env.example`. The working `.env` file is
made from it.

**Windows** (in the Command Prompt, in the bot's folder):

```
copy .env.example .env
notepad .env
```

**macOS / Linux:**

```
cp .env.example .env
nano .env
```

(in nano: save with `Ctrl+O`, Enter; exit with `Ctrl+X`).

Put the token from section 4 into the `TG_BOT_TOKEN=` line, whole, without
spaces or quotes:

```
TG_BOT_TOKEN=7123456789:AAHk3j...the_whole_token
TG_OWNER_ID=
```

Leave `TG_OWNER_ID` empty for now: you'll learn your number in the next step.

The bot replies in Russian by default. For English replies, also add the line
`BOT_LANG=en` now (or send `/lang en` to the bot later). The replies quoted below
are the English ones.

> Why the bot serves no one without `TG_OWNER_ID`: otherwise anyone who finds
> the bot through search could push their files through it on your computer.
> The bot is closed by default and answers only the people you name.

**Find your user_id.** Start the bot (section 6), find it in Telegram by its
username and send it anything. It replies:

> Access to the bot is not configured yet, so it serves no one. Your Telegram
> user_id: `123456789`

Stop the bot and put the number into `.env`:

```
TG_OWNER_ID=123456789
```

and start it again. Now the bot is yours.

The other settings in `.env` are optional:

| Line | Meaning |
|---|---|
| `TG_ALLOWED_IDS=111,222` | who else may use the bot (their user_ids, comma-separated) |
| `REPORT_TTL_MIN=60` | after how many minutes the bot deletes its own reports from the chat; `0` means never |
| `BOT_LANG=ru` | the default reply language: `ru` (Russian, the default) or `en` (English). Each user can change it with `/lang` |

Restart the bot after any change to `.env`.

---

## 6. Starting and stopping

**Start.** In the bot's folder:

- Windows: `python bot.py`
- macOS / Linux: `source .venv/bin/activate`, then `python3 bot.py`

If all is well, the window shows something like:

```
INFO access: users=1, report auto-deletion=60 min
INFO Start polling
INFO Run polling for bot @john_cleaner_bot ...
```

**Don't close** the window: the bot works while it's open.

**Stop.** Press `Ctrl+C` in that window.

**Only one instance.** If you start the bot a second time while the first one
is running, the second exits right away with the message "The bot is already
running in another window or process on this machine". This is a safeguard:
two instances with the same token get in each other's way.

---

## 7. Optional: LibreOffice and exiftool

The bot works fully without them. They add capabilities. The `/status` command
in the chat with the bot shows what was found.

**LibreOffice** is a free office suite. It's needed for the "Regenerate"
button and for cleaning old `.doc` / `.xls` / `.ppt` files.

- Windows and macOS: download it from [libreoffice.org](https://www.libreoffice.org/download/download-libreoffice/)
  and install it like any program. The bot finds it on its own.
- Linux: `sudo apt install libreoffice`.

**exiftool** decodes the hidden vendor data of cameras (MakerNotes) in photos:
serial numbers, shooting settings.

- Windows: on [exiftool.org](https://exiftool.org/) download the "Windows
  Executable" (zip, 64-bit). The archive has an `exiftool-XX.XX_64` folder.
  From it, copy `exiftool(-k).exe` into the bot's `tools` folder,
  **renaming it to `exiftool.exe`**, and the `exiftool_files` folder next to
  it. The result should be:

  ```
  tgbot_metadata\tools\exiftool.exe
  tgbot_metadata\tools\exiftool_files\...
  ```

- macOS: `brew install exiftool` (needs [Homebrew](https://brew.sh/)).
- Linux: `sudo apt install libimage-exiftool-perl`.

After installing, restart the bot and check `/status`.

---

## 8. How to use it

### The main rule: send the file as a "File", not as a "Photo"

If you send a picture as a photo, Telegram recompresses it **before** the bot
sees it, and all the metadata is already gone. The bot will reply that there's
nothing to check.

- **Desktop:** paperclip 📎 → "File" → choose the file. Or drag the file into
  the chat and in the window that appears choose sending **without
  compression / as a file**.
- **Android:** paperclip 📎 → "File". For photos: "File" → "Gallery" inside
  that menu: this way the photo goes without compression (the regular
  "Gallery" compresses).
- **iPhone:** paperclip 📎 → "File". For photos: "File" → "Choose Photo or
  Video": this way the photo goes as a file, without compression.

### The usual flow

1. **Send the file.** The bot acknowledges it, then sends a **report**: what
   was found in the file. The values are hidden under a spoiler: tap the grey
   bar to see them.
2. Under the report there's a **"🗑 Delete report from chat"** button, and
   below it a **"Clean the file?"** message with buttons:
   - **Clean (Stealth)**: the recommended option;
   - **Clean (Paranoid)**: remove absolutely everything;
   - **Regenerate (LibreOffice)**: save the file anew through LibreOffice
     (only for Office documents, ODF and RTF: LibreOffice doesn't regenerate
     PDFs and images, so they don't get this button);
   - **Don't clean**;
   - the **☐ Keep: …** toggles, see below.

   The difference between the options is in [section 9](#9-which-profile-to-choose).
3. **Press a button.** A few seconds later the bot sends a cleaning report and
   a **new file** with a neutral name like `document_3fa9c1.docx`. Save it:
   that's the cleaned copy. The caption under the file says whether the result
   is clean ([section 10](#10-reading-the-bots-replies)).

The buttons live for 30 minutes. After that the bot replies that the button
has expired: just send the file again.

### The "Keep" toggles

Sometimes part of the metadata needs to stay. Press a toggle (it becomes ☑)
and then a cleaning button.

| Toggle | When it's needed |
|---|---|
| Keep: document title | a published report where the title is part of the layout |
| Keep: author | the document is supposed to carry your name |
| Keep: ICC profile (images) | exact color reproduction in print matters |

A kept field stays **exactly as it was**, so the bot honestly says "📌 … the
file is NOT clean: kept by your choice …". That's not an error, it's your
choice. The toggles don't work with "Regenerate": the bot refuses and says so.

### Old .doc / .xls / .ppt

They can't be cleaned in place at all: the metadata and **deleted text from
earlier versions** are woven into the file. So they get one button,
**"Regenerate (LibreOffice)"**: the output is a modern `.docx` / `.xlsx` /
`.pptx` without the edit history. This takes up to a minute. If LibreOffice
isn't installed, the bot says so and leaves the file alone.

### Many files at once: ZIP

Put the files into a ZIP archive and send it. The bot shows how many files are
inside and offers to clean them all. In return you get:

- a report for each file;
- a new archive with **only the files whose cleanliness was verified**.
  Whatever couldn't be cleaned (an unsupported format, for example) doesn't go
  into the archive, so it can't accidentally be passed on as clean.

The **"neutral / original names"** toggle: file names are metadata too
(`Contract_Smith_final.docx`). With neutral names the archive holds
`doc_0001.docx`, `doc_0002.pdf`…, and the mapping from numbers to real names
is only in the report in the chat.

### Commands

Type `/` in the chat with the bot to get the menu.

| Command | What it does |
|---|---|
| `/start`, `/help` | help |
| `/lang` | switch the reply language: Русский / English (`/lang en`, `/lang ru` also work) |
| `/status` | what's available on this machine: LibreOffice, exiftool, auto-deletion |
| `/clear` | delete all the bot's reports from the chat |

### Deleting reports

A report contains the values that were found: your name, paths, coordinates.
They're under a spoiler, but the text is still stored on Telegram's servers.
So:

- the **"🗑 Delete report from chat"** button under every report deletes the
  reports immediately;
- **`/clear`** does the same as a command;
- the bot deletes reports **on its own** after 60 minutes (configurable in
  `.env`).

The bot **never** deletes cleaned files or your own messages. The original
file you sent also stays in the chat: delete it yourself if it's sensitive.
The bot remembers its reports only while it's running: after a restart, delete
old reports by hand.

### The file name

The file name is metadata too, and Telegram shows it to the recipient. The bot
always returns the cleaned file with a neutral name and reminds you if the
original name looks "telling". If you forward the original, rename it.

---

## 9. Which profile to choose

| Profile | What it does | Pro | Con |
|---|---|---|---|
| **Stealth** (default) | removes identity and environment: author, paths, printers, GPS, serial numbers, internal identifiers. Keeps the application and its version | the file looks natural | for Word/Excel/PowerPoint an empty "author" field can itself give away that the file was cleaned; the bot warns about this |
| **Paranoid** | removes everything, including the application and dates | guaranteed clean | it's visible that the file was processed |
| **Regenerate** | saves the file anew through LibreOffice and cleans what's left | no cleaning traces: the file is simply "made in LibreOffice" | the layout may shift slightly; LibreOffice becomes the authoring program |

How to choose:

- **Sending a document to a colleague, a client, a government office**: Stealth.
- **Publishing it, handing it to a journalist or anyone who will dig**:
  Paranoid. What matters more is that nothing is left.
- **A Word document where the cleaning must not be visible**: Regenerate.
- **A phone photo before posting it**: Stealth. It removes GPS and camera
  data but keeps the orientation (otherwise the photo would turn on its side).

---

## 10. Reading the bot's replies

The **inspection report** sorts findings into four groups:

| Icon | Group | Examples |
|---|---|---|
| 🔴 | Identity and personal data | author name, email, GPS, camera serial number |
| 🟠 | Environment | folder paths, computer name, printer, server |
| 🟡 | Provenance | program, version, dates, edit history |
| ⚪ | Structural (usually kept) | document language, outline, color profile |

At the bottom of the report are the **conclusions**: what made the file,
whether there are signs that it was already cleaned or generated by a program,
whether there are contradictions (for example, a modification date earlier
than the creation date).

**The caption under a cleaned file:**

| Icon | Meaning |
|---|---|
| ✅ | clean: a second inspection found not a single original personal value in the new file |
| 📌 | not clean by your choice: you asked to keep something |
| ⚠ | cleanliness couldn't be proven: read the warning above |
| ⛔ | not fully cleaned: some original values remained. **Don't send this file on**: try Paranoid or Regenerate |

If cleaning fails completely, the bot **doesn't send a file** and explains
why. It never passes off an uncleaned file as a cleaned one.

**The detectability line** says whether the file shows that it was processed:

- **the inspector doesn't see any**: the file looks ordinary;
- **noticeable on a closer look**: a careful examiner may guess;
- **obvious**: the file is recognizable as processed or assembled by a program.

---

## 11. Limits

- **File size up to 20 MB.** That's Telegram's limit for bots and it can't be
  bypassed. The archive with a batch cleaning result is up to 50 MB.
- **A picture sent as a "Photo"** has no metadata left: see section 8.
- **A digitally signed PDF** is not cleaned: any cleaning breaks the
  signature. The bot says so.
- **A password-protected PDF** is not cleaned.
- **One job at a time.** While the bot cleans a big archive, the next file
  waits.
- **The bot doesn't** make a document "not written by AI", doesn't change the
  text or layout, doesn't touch version history in the cloud (Google Drive,
  SharePoint).
- **The bot doesn't forge** data: it doesn't write in someone else's name or a
  different date. It only removes.

---

## 12. Privacy: where files go

- The file travels **through Telegram's servers** to the program on your
  computer. Telegram sees the file the same way it sees any forwarded file.
- On the computer the file goes into a temporary folder and is **deleted right
  after processing**. The bot doesn't keep it.
- The bot's window logs only the format, size, number of findings and timing,
  **without file names or contents**.
- Reports are deleted from the chat by the timer and by the button (section 8).
- If a file is truly secret, don't send it to Telegram at all: check it from
  the command line ([section 15](#15-without-telegram-the-command-line)).

---

## 13. Common problems

**The bot doesn't answer at all.**
Check that the bot's window is open and shows no error. The computer must be
on and not asleep. Make sure you're writing to your own bot (the username from
section 4).

**"Access to the bot is not configured yet" / "Access to this bot is restricted".**
`TG_OWNER_ID` in `.env` is empty or holds someone else's number. Put in the
user_id from the bot's reply and **restart** the bot.

**"TG_BOT_TOKEN not found".**
The file isn't named `.env` (Notepad may have saved it as `.env.txt`) or it
isn't in the folder with `bot.py`. When saving in Notepad, choose "Save as
type: All files" and the name `.env`.

**"Telegram did not accept the token".**
The token was copied incompletely, with a space or quotes, or it was revoked.
Copy it again from BotFather's message.

**"The bot is already running in another window".**
Find the first window and close it (`Ctrl+C`), or restart the computer.

**The bot answers every other time or with old messages.**
The same token is running somewhere else, on another computer. Stop it there.

**"No module named aiogram" (or pikepdf, lxml).**
The dependencies aren't installed (section 3, step 3). On macOS/Linux you
forgot `source .venv/bin/activate`.

**"'python' is not recognized as an internal or external command" (Windows).**
"Add python.exe to PATH" wasn't ticked when installing Python. Reinstall it.

**`/status` says LibreOffice isn't found, although it's installed.**
Restart the bot after installing LibreOffice. On Linux, check that the
`soffice --version` command works.

**Regeneration says LibreOffice didn't save the file.**
The file is damaged or LibreOffice doesn't understand it. Close LibreOffice if
it's open and try again, or choose Stealth.

**"Button expired".**
More than 30 minutes have passed or the bot was restarted. Send the file
again.

**The bot replies in the wrong language.**
Send `/lang` and pick Русский or English. The choice is remembered per user.

---

## 14. Updating

If you installed with git (on macOS/Linux: `cd ~/tgbot_metadata` and activate the
venv instead of the first line):

```
cd C:\tgbot_metadata
git pull
python -m pip install -r requirements.txt
```

If you downloaded the ZIP, download the new one, unpack it and **move your
`.env` into the new folder** (and the `tools` folder if you installed
exiftool). Then run `pip install -r requirements.txt` and restart.

---

## 15. Without Telegram: the command line

A file can be checked and cleaned without sending it anywhere. In the bot's
folder (on macOS/Linux, after `source .venv/bin/activate` and with `python3`):

Show the metadata:

```
python -m core.inspect path\to\file.docx
```

Clean it (the result appears next to it, with `.clean` in the name):

```
python -m core.clean path\to\file.docx --profile stealth
```

Profiles: `stealth`, `paranoid`, `regenerate`. No token is needed for this.
The command line always answers in English.

On Windows, if Cyrillic values show up as garbage, run
`set PYTHONIOENCODING=utf-8` before the command (in PowerShell:
`$env:PYTHONIOENCODING="utf-8"`).
