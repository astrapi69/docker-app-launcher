# Quickstart for end users

This guide is for people who just want to **run an app** that ships with
docker-app-launcher. You do not need to be a developer. Follow the five steps
below and, if something goes wrong, jump to the troubleshooting section, which
is organized by the exact problem the launcher reports.

A few words you will meet along the way:

- **Docker** is the program that runs the app in a sealed box on your computer,
  so the app cannot interfere with the rest of your system.
- A **container** is one running copy of the app inside that box.
- An **image** is the packaged app that a container is started from, a bit like
  an installer file.
- A **port** is the numbered door on your own computer through which you reach
  the app in your browser (for example `8080`).
- A **health check** is a small "are you ready?" question the launcher asks the
  app before it tells you the app is up.

---

## Step 1: Install Docker

The launcher needs Docker to be present and running.

- **Windows and macOS:** install [Docker Desktop](https://docs.docker.com/get-docker/).
  Start it once after installing and wait until its whale icon stops animating,
  which means the engine has finished booting.
- **Linux:** install Docker Engine from your distribution, then start the
  service:

  ```bash
  sudo systemctl start docker
  ```

  On Linux you usually also need to be in the `docker` group so your user is
  allowed to talk to Docker. The launcher can set this up for you (see the
  "Set up Docker access" button it shows when needed).

You only do this once. After that, Docker starts with your computer.

## Step 2: Start the launcher

Start the launcher the way the app told you to. Depending on how it was shipped,
that is either a desktop icon, or a command in a terminal such as:

```bash
docker-app-launcher --config launcher.json
```

A single window opens and stays open. It never closes itself. The only way to
close it is the window's X button.

## Step 3: Run "Check system"

Before installing anything, press the **Check system** button. The launcher runs
a short self-test and shows a checklist. Every line is either a green tick
(fine) or a red cross (a problem).

If a line has a problem, a small **problem card** appears above the log with two
short sections written for non-experts:

- **What does this mean?** explains the problem in plain language.
- **What you can do** tells you the next step.

If everything is green, you are ready to install. If not, find the matching
entry in [Troubleshooting](#troubleshooting) below.

## Step 4: Install

Press **Install**. The launcher downloads what it needs and starts the app. The
progress bar and the log show what is happening, line by line. Nothing is
hidden.

The very first install needs an internet connection, because the app has to be
downloaded. After that the app runs offline.

When the install finishes, the status line at the top turns into a green
"running" message.

## Step 5: Open in the browser

Press **Open in browser**. Your default browser opens the app on the port shown
next to the "Port" field. That is it, the app is running.

To stop the app later, press **Stop**. To start it again, press **Start**. To
update to a newer version, see [Updating the app](#updating-the-app).

---

## Updating the app

When the app's authors publish a newer version, updating is a single button.
Press **Update**. The launcher stops the app, fetches the new version, starts it
again, and checks that it answers its health check. Your saved data is kept.

If the new version does not come up correctly, the launcher tells you so and
prints a short note on how to go back to the version that was working, which is
still on your computer.

From the command line the same thing is:

```bash
docker-app-launcher --update
```

---

## Troubleshooting

The launcher classifies every problem into one of eight kinds. Press
**Check system** at any time to see which one applies, then read the matching
entry here. The wording below is the same wording the launcher shows you in its
problem card, so you can match them one to one.

### Docker is not running

**What does this mean?** Docker is the runtime that hosts the app. It is
installed but not running, or not installed at all, and nothing can start
without it.

**What you can do:** Start Docker Desktop or the docker service (Linux:
`sudo systemctl start docker`), then run the system check again. If Docker is
not installed, install it first (see [Step 1](#step-1-install-docker)).

> On Linux you may instead see a permission message. That means Docker is
> running but your user is not yet allowed to use it. Use the launcher's
> "Set up Docker access" button, then log out and back in so the change takes
> effect.

### The app's folder was not found (install_dir)

**What does this mean?** The launcher could not find the app's source folder
(`install_dir`) that this deployment mode needs.

**What you can do:** Set `install_dir` in the launcher config to the folder that
holds the app's files, then run the system check again. If someone else prepared
the config for you, ask them which folder the app lives in.

### The Compose file is missing

**What does this mean?** This app is driven by a Docker Compose file (a file that
describes how the app's containers fit together), and the configured file does
not exist at the shown path.

**What you can do:** Make sure the app files are present under `install_dir` and
the compose file name in the config matches the real file.

### The Dockerfile is missing

**What does this mean?** This mode builds the app from a Dockerfile (a recipe for
building the app image), and none was found at the shown path.

**What you can do:** Point `install_dir` (and `build_context`) at the app's
source checkout that contains the Dockerfile.

### No app image is configured (image_source_declared)

**What does this mean?** Image mode needs to know which app image to run, and
neither an image reference (the address of the packaged app) nor a usable
archive (a saved image file) is configured.

**What you can do:** Set `image_reference` (and optionally `image_archive`) in
the launcher config, then run the system check again. This is normally filled in
by the app's authors.

### A precondition is missing (readiness blocker)

**What does this mean?** A precondition for installing or starting the app is
missing, and the message names the exact gap (for example a missing tool or a
version that is too old).

**What you can do:** Follow the instruction in the message. Every blocker names
its own repair. Then run the system check again.

### The port does not match (port drift)

**What does this mean?** The running container publishes a different port than
the launcher expects, so buttons and browser links would open the wrong address.

**What you can do:** Align `env_port_key` and the `.env` file with the compose
file's port variable, or reinstall the app so the ports match. If you are not
sure, the simplest fix is to press **Uninstall** (your data is kept) and then
**Install** again.

### The app is not answering (health check)

**What does this mean?** The app's container is running, but the app inside does
not answer its health check. The app itself may have crashed.

**What you can do:** Open the app logs (the **App logs** button) to see the app's
own error message, then stop and start the app again.

---

## When you need to report a problem

If you cannot get past a problem, the launcher helps you file a good report:

1. Press **Copy support bundle**. This copies a text summary of your setup
   (versions, mode, state, port, health) with no passwords or private values in
   it.
2. Open the **About** dialog and use its link to the issue tracker.
3. Paste the support bundle into the report.

That gives the app's authors what they need to help you quickly.

---

## See also

- [Consumer integration guide](consumer-integration.md), for authors who want to
  ship their own app with this launcher.
- The project [README](../README.md), for the full configuration reference and
  the command-line interface.


## Cancelling an installation or update

While something is installing or updating you will see a **Cancel**
button next to the progress bar. Cancelling is safe: parts that were
already downloaded stay on your computer and make the next attempt
faster. If you cancel an **update**, the app will be stopped afterwards
(updating stops it first) — press **Start** to run the version you had
before. The same is true for an **internal port change**: it rebuilds the
app, so it stops it first, and cancelling leaves it stopped — your new
port is already saved, so **Start** simply rebuilds with it. If a
cancelled step stops answering, the window frees itself
after a few seconds and tells you; new operations wait until the old one
reports back (at most 10 minutes), and restarting the launcher also
clears this. Your data is kept in all of these cases.


## Closing the launcher

Close the window with the X as usual. If your system has a tray area
and the app is running, the launcher moves there instead of closing —
its tray menu has an entry to quit. Without a tray the X simply closes
the launcher. In both cases the app itself keeps running; start the
launcher again whenever you want to stop it or check on it. If you
close while something is installing or updating, you are asked first:
that step ends, nothing is deleted, and downloaded parts stay for the
next attempt.


## "Reachable from every network" — what that means

If **Check system** shows that the app is reachable from every network,
it means other devices on your network (a shared office or student WiFi,
for example) can open it too. The app has no password, so anyone who
reaches it can read and change your data and use your provider keys.
Normally the launcher publishes the app for **this computer only**, so
you should not see this. If you do, the card tells you what to change;
if you turned network access on deliberately, nothing is broken — just
keep it to networks you trust.
