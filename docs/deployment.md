# Running napper-sync unattended

The intended setup is a small always-on Linux box (a Raspberry Pi is plenty;
a run takes about two seconds of CPU) with a systemd timer. A Windows
scheduled task works too and is described at the end.

## 1. Install

```bash
# as the user that will run the sync (not root)
pipx install napper-sync
# or: pip install --user napper-sync
napper-sync --version
```

Python 3.11 or newer is required (Debian 12 "bookworm" ships 3.11).

## 2. Configure

```bash
napper-sync config init                 # writes ~/.config/napper-sync/config.toml
napper-sync login you@example.com       # e-mailed one-time code
napper-sync babies                      # -> [napper] baby_id
napper-sync famly token <VALUE>         # see "Getting the Famly token"
napper-sync famly children              # -> [famly] child_id
napper-sync config show                 # everything in place?
```

Then dry-run it once and read the output:

```bash
napper-sync sync-famly            # shows what would be written
napper-sync sync-famly --live     # writes
```

### Getting the Famly token

Log in to Famly in a desktop browser, open the developer tools (F12),
Console tab, and run:

```js
JSON.parse(localStorage["famly.accessToken"])
```

Copy the string (36 characters) into `napper-sync famly token <VALUE>`. If
your nursery is on a different Famly instance than `app.famly.de`, check
`localStorage["famly.existingLogins"]` and set `[famly] host` accordingly.

On a headless server, run the browser step on any machine and copy the token
across (`ssh host 'napper-sync famly token <VALUE>'`).

### Secrets and permissions

Everything sensitive sits in `~/.config/napper-sync/` with mode `600`:
`napper-tokens.json`, `famly-token`, and the Discord webhook inside
`config.toml`. Keep that directory out of backups that leave your control, and
never commit it.

## 3. systemd timer

Copy the three units from `examples/systemd/`, adjust `User=` and the paths in
`ExecStart=` / `ReadWritePaths=` to your installation, then:

```bash
sudo install -m 644 examples/systemd/napper-sync.service \
                    examples/systemd/napper-sync.timer \
                    'examples/systemd/napper-sync-failed@.service' /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now napper-sync.timer

systemctl list-timers napper-sync.timer        # next run
sudo systemctl start napper-sync.service       # run once, now
journalctl -u napper-sync -n 30 --no-pager     # what happened
```

Design notes, in case you want to change them:

* **`OnCalendar=Mon..Fri 07..18:00`** - hourly during nursery hours plus one
  run after closing. Nothing is logged at night or on weekends, so nothing
  runs. `systemd-analyze calendar 'Mon..Fri 07..18:00'` previews the schedule.
* **`[sync] days = 4`** - each run looks four days back. Three would make the
  Monday run reach exactly to Friday; four leaves room for entries added
  Friday evening.
* **`Persistent=true`** - runs missed while the machine was off are caught up
  on boot.
* **`OnFailure=`** - if the process crashes or times out, a second unit posts
  the last journal lines to Discord. API-level failures (expired token,
  unreachable host) are reported by the sync itself.
* **Hardening** - `ProtectSystem=strict` and `ProtectHome=read-only`, with
  `ReadWritePaths=` opened only for the config directory. That last line is
  not optional: the Napper id-token is refreshed automatically and written
  back to `napper-tokens.json`. Without write access the refresh fails
  silently a few weeks after installation.

## 4. Discord notifications

Create a webhook in your Discord server (channel settings → Integrations →
Webhooks), put the URL into `[notify] discord_webhook`, and test:

```bash
napper-sync notify            # "Test message"
```

You will then hear about:

* an expired Famly token (with the command to fix it),
* Famly or Napper being unreachable or rejecting a write,
* a crashed or timed-out run (via the `OnFailure=` unit, journal attached),
* every run that imported or corrected something, with the list of entries.

A run that finds nothing new stays silent.

What the webhook cannot tell you: that the machine is off or offline. Silence
is not proof that all is well.

## Windows instead of Linux

`examples/windows-task.ps1` registers an hourly scheduled task that runs
`napper-sync sync-famly --live`. Output goes nowhere by default; if you want
a log, wrap the call in a `.cmd` that appends to a file. The Discord
notifications work the same way.

## Updating

```bash
pipx upgrade napper-sync      # or: pip install --user -U napper-sync
sudo systemctl start napper-sync.service && journalctl -u napper-sync -n 20
```

Config and tokens are untouched by upgrades.
