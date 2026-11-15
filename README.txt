MIA'S ARABIC SCREEN TRANSLATOR
==============================

WHAT IT DOES

This app is made to sit beside Roblox. You pick the Roblox
window, and the app reads the Arabic in the game chat, translates
it to English in real time, and gives you Arabic reply ideas you
can copy and paste back into the chat.

It also works with any other app window, or the whole screen.

It works on any kind of Arabic text — school Arabic (Modern
Standard) and dialects like Egyptian Arabic. Dialects can
translate a little rough sometimes. That is normal.


WHAT'S INSIDE

- Window picker: your open apps in a list. The Roblox window is
  pinned to the top with a game emoji so it is easy to find.

- Roblox chat mode: reads JUST the chat box (top-left of the
  game window) instead of the whole screen, so it is faster and
  does not get confused by other stuff on screen. It turns
  itself on when you pick the Roblox window.

- Live translation: every new Arabic chat line shows the Arabic,
  how it sounds in English letters, and the English meaning.
  English chat lines are ignored — only Arabic is translated.

- Names: chat lines like "Ahmed22: ..." keep the player's name
  on the translation, so you know who said what.

- Egyptian word help: common Egyptian chat words (like ازيك)
  get a tiny plain-English meaning under the translation.

- Clear text mode: makes the chat picture sharper before
  reading it, and the reader throws away words it is not sure
  about. This makes the reading much more accurate.

- Reply ideas: under each message, 3 ideas for what to say
  back — picked by what the message is about (a hello gets a
  hello back, a thank-you gets a you're-welcome, a question
  gets answer-style replies). Every idea shows the Arabic, how
  it sounds, AND the English, so you know what you are saying
  before you send it. "More ideas" shows the next ones.

  Honest note: the ideas come from a built-in list of Egyptian
  phrases picked by simple matching — it is not AI. They are
  real, correct phrases, ready to use.

- Say your own words: type ANY English sentence and get it in
  Arabic, with how it sounds, and a copy button. Not just the
  ideas — anything you want to say.

- Copy buttons everywhere: copy the Arabic reply, or copy the
  English translation, with one click.

- Favorites: tap the star on any reply idea to save it. Your
  favorites stay saved in the app (favorites.json) with their
  own copy buttons, and you can remove them with the X.

- Beep: a little beep when a new Arabic message comes in
  (you can turn it off).

- Overlay: a small floating window shows the newest English
  translations — and the top reply idea — so you can glance
  without switching windows. You can change its text size.

- Screen Translate mode: cover the whole watched window (or
  the whole screen) with English. Every piece of Arabic text
  it can see gets an English label drawn right on top of it,
  where it sits — not just the chat box, everything. See the
  "SCREEN TRANSLATE MODE" part below.

- Save chat log: one button saves everything translated this
  time into chat_log.txt in the app folder.


THE ONE EXTRA INSTALL (READING THE SCREEN)

Python and the app pieces install themselves from
run_translator.bat. But reading text off the screen needs one
more free program: Tesseract OCR, with Arabic language data.

1) Go to the Tesseract at UB Mannheim download page:
   https://github.com/UB-Mannheim/tesseract/wiki
   and download the Windows installer (the 64-bit one).

2) Run the installer. On the "Choose Components" page, open
   "Additional language data" and tick "Arabic".

3) Finish the install. If the translator app is open, press its
   Refresh button (or close and reopen it). The yellow note at
   the top turns into a green "ready" message.

Without Tesseract the app still opens, and the sample sentence
button still works — it just cannot read your screen yet, and
it will tell you so. It never makes up text.


IF THE APP SAYS TESSERACT IS NOT FOUND

The app looks for Tesseract by itself in the usual install
folders (like C:\Program Files\Tesseract-OCR), so most of the
time it just finds it — even though the installer does not put
it on the system path.

If it still says "Tesseract program not found":

1) Press the "📁 Find Tesseract" button under the yellow note
   at the top of the app.

2) Pick the Tesseract-OCR folder itself (usually in
   Program Files). The app remembers it, and the note should
   turn green.

If the note says Tesseract is installed but the Arabic
language is missing, reinstall Tesseract and this time tick
"Arabic" under "Additional language data" on the
"Choose Components" page (see the install steps above).


HOW TO USE IT (WITH ROBLOX)

1) Open Roblox and join your game first.

2) Double-click run_translator.bat. The translator opens.

3) In the "Watch this app" list, pick the Roblox window (it is
   at the top, with the game emoji). Roblox chat mode ticks
   itself on.

4) Press "Start watching".

5) When someone writes Arabic in chat, read it in English in
   the translator (or glance at the overlay). Pick a reply
   idea, or type your own words in "Say your own words".

6) Press the idea's "Copy Arabic" button, click back into the
   Roblox chat, press Ctrl+V, then Enter to send.

7) Press Stop when you are done. "Save chat log" keeps a copy
   of everything that was translated.


SCREEN TRANSLATE MODE

Chat mode reads just the chat box. Screen Translate reads
EVERYTHING.

Tick "Screen Translate: cover the whole screen with English"
and the app looks at the whole window you picked (or the whole
screen). Every piece of Arabic text it finds gets an English
label drawn right on top of it, in the same spot — so you can
read the whole screen like it was written in English.

- Each label is a SMALL black box that fits just the English
  words in it, exactly — nothing bigger. The rest of the
  screen stays see-through, so you always see your game
  underneath. There is never a big black screen.

- The labels update every few seconds. Brand-new text can
  take a couple of seconds to get its label.

- Your mouse clicks pass straight through the labels, so you
  can keep playing like they are not even there.

- The labels only COVER the Arabic on your screen — they do
  not change the game or the app itself. Untick the box and
  everything is back to normal.

- If a piece of text cannot be translated (the internet
  hiccups, or the reading was too fuzzy), that spot is left
  uncovered instead of being covered with wrong words.

- The normal chat translations and reply ideas keep working
  while Screen Translate is on.

- The app hides its own windows from the window picker list,
  so it never tries to watch itself, and its label window
  never shows up as an extra app on your taskbar.


ONE HONEST THING ABOUT ROBLOX AND ARABIC

Roblox itself sometimes shows Arabic letters jumbled or not
joined up, because the game does not draw right-to-left text
very well. The words you paste in are still the correct
Arabic — it is the game's drawing, not your words.


TRY IT FIRST

Press "Try a sample sentence". It runs a built-in Arabic
sentence through the sounds, the translation, and the reply
ideas, so you can see it all working before Tesseract is even
installed. (Translation needs internet for that button.)


MORE HONEST NOTES

- Translating needs the internet. Sounds (transliteration)
  and word help work offline.

- It can only translate text it can SEE on the screen. Clear
  text in apps, videos, and pictures works. Fuzzy text, tiny
  text, and handwriting are hard for it, and sometimes it will
  misread a letter — if a translation looks weird, that is
  usually why.

- It only translates NEW lines once, so the chat does not fill
  up with the same sentence over and over.

- Your screenshots are not saved anywhere. The text it reads
  is sent to Google Translate to be translated, like using
  the Google Translate website.
