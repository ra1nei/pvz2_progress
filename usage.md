## How it fits together

The percentage above is a fraction, and its two halves come from different
places. The top half, how many levels you have finished, lives in the game's
save file: about 3KB, changing every time you play. The bottom half, how many
levels the mod has in total, lives inside its OBB, the big data file the game
ships with, somewhere between 700MB and 1.3GB, changing only when the author
releases a new build.

Nothing here downloads a whole OBB. It uses HTTP Range requests, which ask a
server for a slice of a file rather than all of it. The OBB keeps its table of
contents at the start, so a few scattered megabytes are enough to read every
world map inside. Each count is stored with a fingerprint of the file, so later
runs can tell in one request whether anything changed.

The table is rebuilt by GitHub Actions, so nothing runs on your computer. It
rebuilds every six hours, and also whenever a save is pushed here, which means
finishing a session updates it within a minute or two.

<img src="assets/diagram/pipeline.svg" width="880" alt="The save file becomes the top half of the fraction and the mod's OBB the bottom half; both are read by track.py on GitHub Actions, which rewrites the table">

## Which command, when

Each column below is one situation, read top to bottom. The rest of this page
goes through them one at a time.

<img src="assets/diagram/cases.svg" width="880" alt="Four situations side by side: playing a session, a machine with no mods installed, applying an update, and adding a mod never played before">

## After playing, every time

```
python3 sync.py play
```

This is the whole routine. It connects to the emulator, copies the newest saves
onto it, starts it if it is not running, then watches. Each time you leave a
mod, that mod's save goes up straight away, and everything is swept once more
when the emulator closes. Those uploads are what refresh the table.

Uploading as you leave each mod rather than only at the end means a session
that ends badly costs you at most the mod you were in, not everything you
played that sitting. A push that fails, on a dropped network say, is reported
and stepped over: the commit is already here and rides up with the next one.

The download happens once, at the start, and deliberately not again while you
play. A game reads its save when it launches, so a file arriving underneath one
that is already open is thrown away when you quit it. Syncing at the start is
what makes every mod you open that session current; playing the same mod on two
machines at once is the case nothing can fix, and the refusal to push a save
holding less progress is what catches it.

The reason it fetches saves *before* letting you play is the point of the whole
script. Say you reached level 80 on your desktop yesterday, then sit down at
your laptop today, whose save still reads level 65, and play without syncing.
On exit the laptop uploads its file and level 80 is gone, with no warning: to
any file-sync tool, the laptop's copy is simply the newer one. So `play` refuses
to start the emulator at all if it could not fetch the newest saves first.

One thing to avoid: do not run `sync.py pull` with a mod already open. The
game holds progress in memory and writes it out on exit, so a save placed
underneath a running game is wiped the moment you quit. `play` gets the order
right on its own, which is why it is the one to use daily.

## A machine with none of the mods

```
python3 install.py auto
```

It reads `saves/` to see which mods you play, then downloads each one's APK and
OBB, installs both, and puts your latest save in place.

A freshly installed app has not made its save folder yet, so the folder is
created and the save dropped straight in; the game reads it on first launch.
One command takes a machine from nothing to the same progress as the other one.
After that, `play` handles everything.

## One mod at a time

```
python3 install.py install cld
```

Use this for a single mod, or to put back one you uninstalled to save space.
Uninstalling loses nothing permanently, since the save lives here in `saves/`.

## Checking for updates

```
python3 install.py status
```

Prints the version installed here next to the version being published, plus the
size of the OBB on the device. When they differ, install it with the command
above.

Two things update independently, which is worth keeping straight:

- **The level count in the table** updates itself. When a mod publishes a new
  build on GitHub Releases, the next run re-reads the count. That is what the
  blue badge shows, and its version is the release it last read.
- **The mod on your machine** does not. Nothing reaches into your emulator
  uninvited; you install updates when you feel like it.

So the table reading v1.4.2 while your copy is on v1.4.0 is normal, not a bug.

### When the install is refused

`adb install -r` gets rejected when the new APK is signed with a different key,
which mod rebuilds often are. Android will not let one app replace another
unless the signatures match. Then:

```
python3 install.py install cld --force
```

`--force` uninstalls first, which is the only way past a signature change.
Normally that would delete your save with it. Not here: the save is copied off
the device before any uninstall and put back after, preferring the copy in
`saves/` since that is what your other machine last played.

## A mod you have never played

Play a mod on any machine and its save reaches GitHub by itself, because
`sync.py` pushes the save of every mod installed on that device, known or
not. The mod then appears in the table straight away, but with no numbers: the
save says you play it, and nothing more. Counting its levels means reading its
OBB, which can only happen on a machine that has the mod installed, and knowing
where to download it later is something no save file records.

So a new mod needs a couple of things done once, on the machine that has it.
Until then the table shows the row and the run summary says which mods are
waiting.

1. Install it yourself, however its author distributes it, and make sure its
   OBB really landed. An APK on its own gives an app that cannot start, and
   with no OBB there is nothing to count; that is what `No OBB for ... on
   device` means.
2. Count its levels, and let it name itself: `python3 addmod.py`
   The name comes from the mod's own APK, so nothing has to be typed: Addendum
   answers `PvZ2 Addendum` and is filed as Addendum. Pass `--name` only if that
   comes out wrong. Reading the label needs `aapt2`, which ships with the
   Android SDK next to `adb`.
3. Add its download page to `links.json`, keyed by the package suffix
   (`com.ea.game.pvz2_cld` uses the key `cld`).
4. Find its APK and OBB: `python3 install.py scan`
5. Commit `links.json`, `install.json`, `sources.json` and the new `worlds/` file.

### More than one version to download

Collided offers 30 and 60 FPS builds, Fallen 32 and 64 bit. The scan will not
choose for you, since choosing wrong installs something you did not want:

```
python3 install.py pick cld "60_FPS"
```

The choice survives future scans, and only comes up again if files get renamed.

### Mods not on Drive or GitHub

Requiem puts MediaFire links inside a text file, Spice publishes on itch.io.
Neither can be scraped reliably, so paste a direct `.apk` link into that mod's
`apk_url` field in `install.json`.

To move a mod from the amber badge to the blue one, give its OBB a GitHub
Releases link in `sources.json`, shaped like
`https://github.com/OWNER/REPO/releases/download/TAG/FILE.obb`. Other hosts are
rejected on purpose: the update check works by asking the GitHub API for the
newest release and cannot do that elsewhere. A blue badge on a mod nothing is
actually watching would be worse than no badge.

## Trying it without a second machine

A bare Android emulator stands in for a machine that has none of the mods, so
the install path can be exercised without touching the one you actually play
on. It needs a JDK, which Android Studio bundles, and an ARM system image.

```
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
export ANDROID_SDK_ROOT=~/Library/Android/sdk
sdkmanager --install "system-images;android-33;aosp_atd;arm64-v8a"
avdmanager create avd -n mayB -k "system-images;android-33;aosp_atd;arm64-v8a"
~/Library/Android/sdk/emulator/emulator -avd mayB -no-window -no-audio -port 5580 &
```

Then clone this repo somewhere fresh and work in that copy, passing
`--device emulator-5580` so nothing reaches the real emulator by accident.

The mod will not actually run there: an ATD image has no GPU. Installing,
pushing the OBB and restoring the save all work, which is the part worth
checking. To rehearse the restore, put any save at the mod's path first, since
that is what opening the game once would otherwise do for you.

Delete it afterwards with `avdmanager delete avd -n mayB`; the system image is
several gigabytes and lives under the SDK.

## The two columns

**World** is what the game draws on its world maps, and is the number that was
checked against the game itself. The bar sits after both columns and reports
both, so a mod with its worlds finished and its quests barely started does not
draw the full green bar the world column alone would give it.

**Quest** is everything reachable only through the quest registry, which every
mod ships as `QUESTS.RTON` beside its world maps. That is where the Epic
section lives: Addendum's Holly Barrier is six levels there and appears on no
map at all. A dash means there is nothing to count: Requiem ships no registry
at all, while Alternate UniverZ has one whose quests are all either switched
off, repeating seasonal events, or levels its world maps already show.

The two are kept apart instead of added together, for two reasons. Quests often
point back at levels already on the map, so adding them would double-count;
those are subtracted, and for Addendum that removed 57 of 81. And progress in
the two moves differently: the save records map levels one at a time, but for a
quest chain it records only that the whole chain is finished, so a chain half
played still reads as nothing done.

Levels a quest reaches that belong to a world rather than to a chain, like
Requiem's end-of-world bonus levels, are added to World instead. Daily
activities, piñata hunts, timed events and limited-time replays are dropped
entirely.

World also covers worlds that are hidden from the main map but opened by a
gate, which is how Reflourished's Travel Log works: its sixteen worlds are
reached through a hub rather than from the map, and they are real progress.
A hidden world nothing points at stays out, being leftover vanilla data. The
fifteen Travel Log worlds that replay past limited-time events are dropped for
the same reason the LTEReplay quests are; keeping them left Reflourished at
1128 of 1409 when in fact it is finished.

## What counts as a level

The numbers are meant to match what the game shows on each world map, which
took a few corrections to get right.

A level counts when it is a node on a world map, its type is `level`, and it
does not point at a Danger Room. Danger Rooms are excluded because the game
excludes them from its own totals.

Nodes are counted rather than distinct level IDs, because several map tiles can
share one ID and the game still counts each tile. Addendum's Egypt is the clear
case: 44 tiles, 40 IDs, and the game shows 44.

Hidden worlds do not count; they are usually leftover vanilla content still in
the files but unreachable. Neither do the worlds behind a Travel Log hub, which
ship no world map of their own, so their levels cannot be counted at all.
Reflourished keeps several hundred there, and every run notes underneath the
table which mods have content it cannot see.

## What the messages mean

| Message | What is going on |
|---|---|
| `No device` | The emulator is not running, or adb has not connected. The scripts try the usual ports themselves; if that fails, start the emulator first. |
| `NOT FOUND` from `find` | That mod has never been opened here, so it has no save yet. Open it once. |
| `REFUSED: device has N cleared, saves/ has M` | This machine has less progress than the stored copy, so it played on an old save. Nothing was overwritten. Sync and replay, or use `--force` if you are sure this copy is the one to keep. |
| `Pull failed, not starting the emulator` | Saves could not be brought up to date, so playing now risks losing another machine's progress. Usually network or git. |
| `this APK changed since last time` | The file at that link is not the one installed before. Nearly always a new build, but it is also what a swapped file looks like, so it gets reported. |
| `GitHub rate limit hit` | Anonymous API calls are capped at 60/hour. Progress still updates; only the release check is skipped that run. |
| `Folder is no longer public` | Only the old Drive path, now a fallback. Saves normally come from `saves/`. |
| `Unrecognised Drive folders` | Also the fallback: a Drive folder that matches no known mod. |

## What each file does

The four scripts at the top are the things you run. `pvz/` is what they are
built out of, grouped by what it touches.

| | |
|---|---|
| `sync.py` | Moves saves between the emulator and `saves/`. The daily one. |
| `install.py` | Installs and updates mods on a machine. |
| `addmod.py` | Sets up a mod the repo has never seen. |
| `track.py` | What GitHub Actions runs: reads saves, checks releases, rewrites the table. |
| `pvz/rton.py`, `pvz/rsb.py` | PopCap's binary JSON and archive formats. Everything depends on these. |
| `pvz/worlds.py` | Counts a mod's levels from its OBB. The counting rules live here. |
| `pvz/quests.py` | The other place levels live, and which of them are worth counting. |
| `pvz/save.py` | Reads a save file and works out what has been finished. |
| `pvz/totals.py` | Rolls every mod's counts into one file. |
| `pvz/github.py` | Asks GitHub whether a mod published a new build. |
| `pvz/drive.py` | Reads a public Drive folder. |
| `pvz/device.py`, `pvz/apk.py` | Talking to an emulator, and reading an installed APK. |
| `pvz/net.py` | HTTP, and the differences between operating systems. |
| `links.json` | Where each mod is published. Hand-maintained. |
| `install.json` | Which APK and OBB to fetch. Written by `scan`. |
| `sources.json` | OBB links pulled from each APK while the game was installed. |
| `worlds/*.json` | Level counts per mod. Generated, so not worth editing. |
| `saves/*.dat` | The save files themselves. |
