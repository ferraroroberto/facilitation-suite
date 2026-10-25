# Planning

The **Plan** tab edits `session.yaml`: sections with planned minutes (drag to reorder, rename, collapse — or **Collapse all / Expand all**; every load opens them all), and inside each section the slides, activities and breaks in order (drag, or Alt+↑/↓, or Move up/down). **Add slide or activity** at the end of a section also adds a **section after this one**. Each item has:

- a **Camera layout** (the item's OBS profile: Camera strip / Camera PiP / Screen only; slides start with the detected one),
- its **own timer** — no global defaults, decided item by item: duration, when it starts (manually, when the item opens, with the capture), where it shows (stage / presenter / both) and what happens at 00:00 (keep showing 00:00, remove the timer from the stage, stop the capture, next item, chime); on the stage the timer takes the colour of its state — black before it starts, **green** while it runs, **yellow** paused, **red** at 00:00 (the pill on an activity or slide takes it as its fill, the big clock on a break or breakout as its text; set them under [Stage lettering](#stage-lettering)); a paused timer is yellow on the presenter and the phone too; on a slide it sits in a bottom corner over the slide (the left one when the camera takes the right),
- its **music** (optional — see [Music](music.md)): a track from the session's `audio/` folder that plays with the item's timer or when the item opens,
- **In this session** (off = **hidden**, like PowerPoint's *Hide slide*: kept in the plan, dimmed with an eye-off mark, skipped by Next and Previous live but still reachable from the presenter's filmstrip). The row's **⋮ menu** (*Hide slide* / *Show slide*, by kind) and the **H** key on a selected row flip the same switch, for the whole selection when several rows are picked; a hidden item's time stays out of the planned timing,
- for a **breakout** (Add slide or activity → Breakout): its title, the round in the rooms (Pairs, Groups of 4 · A or B — the stage says it with the room count from the Groups tab) and its clock, ten minutes by default,
- for activities: type, title (the name in the plan and on the presenter; the question when empty), question, question font (the session's stage font, or one installed on this PC) and size (in stage pixels on the 1920×1080 canvas), the prompt to paste in the chat, and the type's own answer options,
- **Notes** for the presenter's Notes card — a slide starts from its PowerPoint notes, and editing them here leaves the deck as it is (clear the box to get the deck's back).

Type `\n` in a question or title where the line should break on the stage; lists, the presenter and the results show it on one line. **Duplicate** (or Ctrl+D) copies the selected items right after the last of them. The "who are you with?" preview draws the rooms from the Groups tab (it redraws after a shuffle), and the stage shows the round and room count under its title.

Select several items as in a file manager — **Ctrl+click** adds or removes one, **Shift+click** selects a range (Ctrl+Shift+click adds it), **Shift+↑/↓** extends, **Ctrl+A** selects all, **Esc** keeps one — and the editor becomes a bulk panel: one **Camera layout** or **In this session** for all of them, **Duplicate** or **Delete**. Dragging any selected item moves the whole selection; **Delete** on the keyboard deletes it. Deleting an imported slide only takes it out of the plan: it stays in the deck, and **Add slide or activity → Slide from the deck** brings it back.

## Stage lettering

Set in the Sessions tab → an open session's **Session settings** → *Stage look*, with two fonts. The **title font** letters titles and questions: Patrick Hand, a font installed on this PC, or **Font file…** (an `.otf`, `.ttf` or `.woff` on this PC, your own handwriting font, say), its **weight** (regular / bold) and **line thickness** in stage pixels (a stroke under the letters, so they keep their shape). The **text font** letters everything else — by default the chat hint's plain sans, or an installed font, regular or bold. Then **each kind of text** takes either font, in **ALL CAPS** or **as typed**: titles and questions (the title font, in capitals, by default), subtitles, the chat hint ("Write your answer in the chat"), the answers (word cloud, cards, feed, scale, map, groups) and a slide's text other than its title (all four in the text font, as typed, by default). A slide's title is the box PowerPoint marks as its title, else its biggest text. It all goes into `session.yaml` (only what differs from the defaults):

```yaml
font:
  file: C:/Users/you/Fonts/MyHand-Regular.otf   # else family: Georgia
  weight: 400
  caps: true              # titles and questions in capitals
  title_color: "#c62828"  # colour of activity titles and questions (empty = the theme's ink)
  title_size: 84          # their size in stage px (0 or absent = 72)
  timer_idle: "#1f1f1f"   # the stage timer before it starts, while it runs, paused, at the end
  timer_running: "#00a44e"
  timer_paused: "#f2b705"
  timer_done: "#c40c0c"   # each empty or absent = the theme's colour (the four shown)
  caps: true              # titles and questions in capitals
  text_family: Georgia    # empty = the chat hint's plain sans
  text_weight: 400
  roles:                  # title, sub, hint, answers, slide_text
    answers: {caps: true}           # the word cloud in capitals
    hint: {font: title, caps: true} # the chat hint in the title font
```

The title **colour** and **size** letter activity titles and questions, beside a camera strip too; imported slides keep their PowerPoint size and colour. The editor shows the colour's contrast on the stage background and warns under 3:1 (a pale colour on the light grey is hard to read from a distance); the theme's own ink is 14:1. Set them for every new session in **Settings → Stage defaults** (the same editor), and per presentation on the session's own card — a session keeps what it was made with until you change it there.

## Timer colours

The same editor, under *Timer colours*: four pickers (not started, running, paused, ended) and **Theme colours** to put them back. The stage timer wears the colour of its state: on an activity's or a slide's **pill** it is the fill, with white digits (dark digits on a fill white cannot hold 3:1 on, the yellow), on a break's or a breakout's **big clock** it is the text. Defaults: black `#1f1f1f`, green `#00a44e`, yellow `#f2b705` (the paused clock is that yellow darkened by a third, since the yellow itself is 1.6:1 on the light stage), red `#c40c0c`. In `session.yaml` they are `font.timer_idle` / `timer_running` / `timer_paused` / `timer_done` (`#rrggbb`), copied from **Settings → Stage lettering and timer colours** into a new session, and a session's own wins; the stage themes and the plan previews both follow.

Every item follows it; in the Plan tab an item's title **font** row (font, size, capitals — "as the session" by default) and its **Other text** rows (the chat hint and answers of an activity, a breakout's subtitle, a slide's text: font — the title font, the text font or an installed one — and capitals) are the exceptions for that item only (`font.roles` on the item).

Each of the font, size and capitals rows says where its value comes from, in three layers: the **global** default (Settings → Stage defaults), the **presentation** (this session's Stage look) and **this item**. An inherited value is dimmed in a dashed box with a chip — `Global`, or `Presentation · global is 72` when the session differs; an item's own value is highlighted, with `This item · presentation is 90` (or `This item · matches global`). **Reset to presentation** removes the item's own value so it follows again; **Reset to global**, shown whenever the value in effect differs from the global one, saves the global value on that item only (the presentation and every other item stay as they are; an item cannot hold a font file or the theme's own font, so for a font row it appears only when the global font is an installed one). A slide has no size row — *Sizes come from the PowerPoint slide* — but its font and capitals rows work the same. In the plan list, an item with any lettering of its own carries a small mark (its size, else *Own font*) whose tooltip names what it overrides, e.g. "Overrides: size 120 px (presentation: 72 px)".

On the Stage look, the title font, size and capitals each show **Differs from global (N)** with a **Reset to global** while the session's value differs from Settings → Stage defaults. **Reset all to defaults** copies the whole default stage look (theme and lettering) back into the session after a confirm: every item that follows the presentation changes with it; items with their own value keep it.

The file stays where it is (keep it on this PC — the readiness list warns if it goes missing, and the stage then falls back to Patrick Hand); it is served to the stage, the presenter's previews, the phone remote and the frozen captures from `/api/sessions/<id>/font`.

Activity types are plug-ins: one folder per type under `app/activities/<type>/` with an `editor.json` (label, icon, options). Edits are staged; **Save to session.yaml** is the only write.
