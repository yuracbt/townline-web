# TownLine Web — Command Cheatsheet

Run everything from the `townline-web` folder in Terminal.

## Everyday commands

| Command          | What it does                                                     |
|------------------|------------------------------------------------------------------|
| `./tl.sh run`    | Run the app on your computer → http://localhost:5000 (or :5001 if 5000 is busy) |
| `./tl.sh update` | Download the newest code from GitHub                             |
| `./tl.sh deploy` | Put GitHub's code on the Oracle server and restart the app       |
| `./tl.sh logs`   | Show the app's recent log on the Oracle server                   |
| `./tl.sh ssh`    | Open a terminal on the Oracle server                             |

The usual cycle: `./tl.sh update` → `./tl.sh run` (test it) → `./tl.sh deploy` (go live).

## Getting updates (the manual way)

```bash
cd ~/townline-web
git pull
```

If git complains that your local changes would be overwritten:

```bash
git stash      # shelve your changes safely
git pull       # get the updates
```

## Pushing your own changes to GitHub

```bash
cd ~/townline-web
git add -A
git commit -m "Describe what you changed"
git push
```

The first `git push` asks for your GitHub username and a **personal access
token** (GitHub no longer accepts your password here). Create one:
github.com → your avatar → Settings → Developer settings →
Personal access tokens → "Generate new token (classic)", tick the `repo`
checkbox, generate, and paste it as the password. Your Mac remembers it
afterwards.

Then make it live: `./tl.sh deploy` (the server pulls from GitHub, so push first).

## Oracle server details

- Site: http://40.233.115.145/
- SSH key on your Mac: `~/.ssh/townline-web-2.key`
- App files on the server: `/opt/townline-web`
- Database on the server: `/opt/townline-web/townline.db`
- Restart the app by hand: `sudo systemctl restart townline`
- Check it's running: `systemctl is-active townline`

## Troubleshooting

- **Port 5000 in use (macOS AirPlay)**: `run.sh` picks a free port on its own —
  open the address it prints (usually http://localhost:5001).
- **`Permission denied` on `./tl.sh`**: `chmod +x tl.sh run.sh`, then try again.
- **Deploy says "SSH key not found"**: one-time setup — Oracle Console →
  Cloud Shell, run `cat ~/.ssh/ampere-watch`, save the output to
  `~/.ssh/townline-web-2.key`, then `chmod 600 ~/.ssh/townline-web-2.key`.
- **Start over locally**: stop the app (Ctrl+C), delete `townline.db`,
  run `./tl.sh run` again.
