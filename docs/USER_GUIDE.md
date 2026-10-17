# POPPYS AI — staff guide

POPPYS AI reads a buyer's order file and fills the Axpert sample order entry for you. Nothing goes into Axpert until an approver clicks **Accept and save to Oracle**.

Open it in Edge or Chrome: **http://127.0.0.1:8008**

## 1. Sign in

- Type your username and password, then click **Sign in**.
- **The first time** (or after the administrator reset your password) you are asked to choose your own password: type the one you were given, then a new one of at least 12 characters. A short sentence is easiest to remember, for example `blue tractor seventeen`.
- After 30 minutes without using it, you are signed out. Sign in again on the same screen: what you were typing is still there. Drafts you left are under **Recent files**.
- Too many wrong passwords make you wait. After 20 wrong tries the account is locked: ask the administrator to unlock it.

## 2. Upload the buyer's file

1. Click **New order**.
2. Drag the file onto the page, or click to choose it. Any Excel file works (`.xlsx`, `.xls`, `.xlsm`, `.xlsb`, `.ods`, Excel XML or web-page exports), and so do CSV and JSON. Files up to 12 MB.
3. POPPYS AI lists what it found. Click **Open the order entry →**.

If the file is refused, the message says why. For example:
- **Password-protected:** open it in Excel, remove the password, save, then upload again.
- **"This computer is missing a part POPPYS AI needs":** the file is fine. Ask the administrator.

## 3. If the layout is new

The first time a buyer's layout is seen, POPPYS AI shows where it thinks each field is: buyer, style, PO, dates, the size table.

1. Check each field. Each one shows the cell it came from (for example `PO!C4`) and why it was chosen.
2. Change anything that is wrong, then click **Check where the order is in this file**. A wrong choice is marked in red with the reason, such as "H3 holds NEXT, which is not a date".
3. Save it as that buyer's layout. Next time, the same buyer's file is read automatically.

**Read this file differently** brings this screen back at any time.

## 4. Review

The screen looks like the Axpert entry screen: the order details on top, then the size × colour table.

- A small grey label next to a value shows the Excel cell it came from. Hover over it to see more.
- **Red outline:** something is missing or unclear. The reason is written under the field. Common questions:
  - **"Are these pieces or packs?"** Choose Pieces or Packs. For packs, type how many pieces are in one pack.
  - **"Could be 3 April or 4 March"**: type the right date.
  - **"Written with a dot between thousands"**: type the quantity as a whole number, for example `2000`.
- **Show me the next one ›** jumps to the next red field.
- Your corrections are saved in the draft when you click **Check & continue**. If you close the browser tab before that, the browser asks first, because unchecked corrections would be lost. When nothing is red, **Check & continue** takes you to the approval step.

## 5. Check & approve

POPPYS AI shows the exact records it will create in Axpert, and the total pieces.

- Read the yellow box **Please read before approving**, if there is one. It lists what the file itself says that may need a second look: a total row that does not match the sizes, a second ship date, rows hidden in Excel, the same order on two sheets. Tick **I have read these notes**: the approve button works only after that.
- If anything is wrong, click **← Back to the order entry** and fix it.
- Only people with the **approver** role can click **Accept and save to Oracle →**. If the administrator has turned on the four-eyes rule, the approver must be a different person from everyone who uploaded or changed the order.

## 6. Saved

- The diagram shows each record that was saved, read back from Oracle. A ✓ means Oracle holds exactly what you approved.
- Click a box to see its rows. **Open in Database** shows the full order.
- **Start another order** begins again.

If saving fails, **nothing** is saved. The message says why. Fix it, then approve again.

## Ask AI

Click **Ask AI** and type a question in plain words. For example:
- "size wise quantity for order PK/26-27/S/9001"
- "buyer wise order quantity this year"

Each answer shows the table, how the tables are linked, and the exact query. Ask AI only reads. It can never change anything.

## Database

**Database** shows the Axpert tables with business names. Search for an order reference to see the order, its lines and its sizes together.

## When something goes wrong

- A message with **Reference** and a code: tell the administrator the code. It points to the exact moment in the log.
- The **Oracle** light at the bottom of the menu is red: the database is not reachable. You can still upload and review, but not save. Ask the administrator to start Docker Desktop.
- Never send the buyer's file, or your password, to anyone outside POPPYS.

---

## சுருக்கமாக (Tamil quick guide)

1. **New order** → buyer Excel file-ஐ இழுத்துப் போடுங்கள் → **Open the order entry →**
2. சிவப்பு கோடு உள்ள field-களை சரி செய்யுங்கள் (pieces-ஆ packs-ஆ, தேதி, quantity). **Show me the next one ›** அடுத்ததைக் காட்டும்.
3. **Check & continue** → **Please read before approving** பெட்டியைப் படியுங்கள்.
4. **I have read these notes** என்பதை tick செய்யுங்கள். Approver மட்டும் **Accept and save to Oracle →** அழுத்தலாம். அதுவரை Axpert-ல் எதுவும் சேமிக்கப்படாது.
5. சேமித்த பிறகு diagram-ல் ✓ இருந்தால், Oracle-ல் நீங்கள் approve செய்தது அப்படியே உள்ளது.
