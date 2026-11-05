# zombieports

**What's running on port 3000?** zombieports shows every port listening on your machine, which project started it, how long it's been up, and which ones look forgotten. One key kills them.

```bash
uvx zombieports
```

<p align="center"><img src="https://raw.githubusercontent.com/ArkaKhorchidian/zombieports/main/docs/demo.gif" alt="zombieports demo" width="800"></p>

<sub>Real dev servers (Next.js, Flask, Uvicorn, Redis, a forgotten <code>http.server</code>) running on Linux. Pressing <b>z</b> kills the three that look abandoned.</sub>

You know the drill: `Error: listen EADDRINUSE :::3000`, then `lsof -i :3000`, then squinting at a PID, then `kill -9`. Or the Vite server from last Tuesday that's still holding 5173. zombieports puts all of it in one screen.

## What it shows

| Column | |
|---|---|
| **Port** | Every TCP port in LISTEN state |
| **Project** | The project the server was started from: `package.json`'s name, or the folder holding `pyproject.toml`, `Cargo.toml`, `go.mod`, `.git`… |
| **What** | Recognises 50+ things from the command line: Next.js, Vite, Nuxt, Astro, Storybook, Uvicorn, Django, Flask, Rails, Jupyter, Postgres, Redis, Docker, Ollama, AirPlay Receiver (the reason port 5000 is taken on a Mac)… |
| **Up for / PID / Memory** | How long it's been running and what it costs |
| **Status** | `open to your network` when a dev server or database listens on all interfaces, and `zombie?` when it looks abandoned |

### What counts as a zombie

A dev server started from one of your projects is flagged when:

- **its terminal is gone**: the shell that started it exited and it got re-parented to `init`/`launchd`
- **its folder was deleted**: it's serving a project that no longer exists
- **a newer copy is running**: two `next dev` servers for the same project, the older one is almost certainly forgotten
- **it's been up for more than a day**

Databases, Docker, and apps the OS launched are never flagged.

## Keys

| Key | |
|---|---|
| `k` / `Delete` | Stop the selected server and its child processes (SIGTERM, then SIGKILL after 3 s) |
| `K` | Force kill straight away |
| `z` | Kill every zombie (asks first) |
| `o` | Open `http://localhost:PORT` in your browser |
| `a` | Show other users' and system listeners too |
| `/` | Filter by port, project, or kind |
| `q` | Quit |

## Without the UI

```bash
zombieports list                # print the table
zombieports kill 3000 5173      # free some ports
zombieports kill --zombies      # free every abandoned one
zombieports --json              # for scripts
```

## Platforms

macOS, Linux and Windows, no `sudo` needed for your own processes. It uses [psutil](https://github.com/giampaolo/psutil) rather than parsing `lsof`/`netstat`. Other users' and system processes are listed with `-a`, but you can only kill your own.

## Install

```bash
uvx zombieports          # run without installing
pipx install zombieports # or install it
```

Requires Python 3.9+. Built with [Textual](https://github.com/Textualize/textual).

## License

MIT
