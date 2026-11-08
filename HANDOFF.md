# Handoff: Garmin watch app for the training tracker

Claude: read this file and pick up where it leaves off. The user is not a software developer, so explain things simply. Ask before installing anything.

## Goal
Show this week's training progress on the user's **Forerunner 965**, so they can check it on the go without a computer. Today, `tracker.py` pulls runs from Garmin Connect and builds a web page on a Mac. The watch app should read runs directly from the watch, so it needs no Garmin login.

What the watch screen should show (small, glanceable), for example: "Week 1: 18.5 / 30 mi · Sat 11 left · strength ✓".

## Options ruled out, and why
- **Phone web page fed by the Garmin login key:** that key lasts about a day and gets replaced on use. A scheduled online job would break as soon as it missed saving a replaced key. Garmin's sign-in is also unofficial and has changed before.
- **Strava API:** requires a paid Strava subscription since June 2026.
- **Native iPhone app (Apple Health):** too much work, and needs a $99/yr Apple developer account or re-installing every 7 days.
- **Building on the original laptop:** that machine can't copy files to the watch over USB.

## What we confirmed (Connect IQ, Garmin's watch-app platform)
- Garmin's developer account is free. As far as we know you just sign in with the normal Garmin account and accept the developer terms. **Not confirmed yet:** if it asks you to apply or pay, stop and tell the user.
- An app can read past activities through `UserProfile.getUserActivityHistory()` (API 3.3.0+). Each entry has **date, distance (meters), duration, and sport type**. Any of these can be null.
- **There is no elevation/ascent field**, so the tracker's ft/mi climb numbers won't carry over to the watch. The user is aware.
- The user's watch keeps history back to at least August 2026, which covers the whole plan.
- **Risk:** one forum report says the history came back empty on a real FR 965 even though it worked in the simulator. Test this first with a tiny app that just shows "found N runs since Oct 26".
- **Not checked yet:** which sport type Garmin's "Cardio" activity (the strength days) shows up as.

## Getting the app onto the watch
1. **USB sideload:** copy the built `.prg` file into `GARMIN/APPS` on the watch. On Windows the watch shows up like a drive. On a Mac you need OpenMTP, and some 965 owners report the watch never shows up. **First step: plug the watch in and see whether the computer sees it, before downloading anything.**
2. **Backup, no USB:** upload the app as a private beta on the Connect IQ developer dashboard, click Download for your device, and it installs on the next phone sync. Garmin has an open bug where this doesn't install.

## Downloads needed (user has OK'd these in principle; confirm free disk space first)
- Connect IQ SDK, for the FR 965 only. Current size unknown; the installer shows it before downloading.
- Java (JDK, about 185 MB). The SDK's compiler needs it.
- Optional: run these inside a Linux VM to keep them separate. Garmin's graphical SDK Manager doesn't start on recent Ubuntu, and a community command-line tool (lindell/connect-iq-sdk-manager-cli) works around that.

## Plan data the watch app needs
The `PLAN` list in `tracker.py` is the source of truth: week number, Monday date, target miles, and planned miles for Tue/Wed/Thu/Sat/Sun. Wed is optional in weeks 0-4. Weeks with no target (recovery and race week) show a message instead. Strength days are Tue (strength) and Thu (ankle work), logged on Garmin as "Cardio". `dynamic_plan()` re-spreads the remaining miles once runs come in. That's nice to have on the watch, but not required for a first version.

## Next steps
1. Plug the watch in and see whether the computer sees it.
2. Set up the developer account, then install Java and the SDK.
3. Build the "found N runs" test app and put it on the watch.
4. If it works, build the real weekly-progress screen.
