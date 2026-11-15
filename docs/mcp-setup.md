# Hometown Table MCP: setup

The MCP lets Claude answer "where should we go tonight?" from the family's list, look up places and visits, and (if
you allow it) log visits and sort the inbox. It is a second service, `hometown-mcp`, next to the app:

```
claude.ai / Claude Desktop / Claude Code
        |  OAuth through Cloudflare Access (managed OAuth)
        v
Cloudflare Tunnel  https://hometown.example.com/hometown/mcp
        |  every request carries a signed Cf-Access-Jwt-Assertion
        v
hometown-mcp  127.0.0.1:8783  (mcp_http.py: checks the assertion, then runs the tool)
        |  Authorization: Bearer <key>, X-Hometown-As: <the signed-in email>
        v
hometown      127.0.0.1:8782  (the app: the same checks and rules as the phone)
```

The MCP service has no access to the database: it runs as its own user and calls the app's API on loopback with a
bearer key. The app accepts the keys only from loopback, so the keys are useless anywhere else. Every write goes
through the same validation as the phone, and the MCP never hands an editor's email to Claude.

All the commands below run as root on the container. Placeholders: `hometown.example.com` is the app's public
hostname, `<team>.cloudflareaccess.com` your Zero Trust team domain.

## 1. The tunnel route

In Cloudflare Zero Trust, **Networks, Tunnels**, open the tunnel that serves the app and add a public hostname on the
same hostname as the app, with the path `hometown/mcp`, pointing at `http://<container>:8783`. Keep it above the app's
own route, so the more specific path wins.

Once Access is configured (below), the service listens on every interface of the container, as the app does, because
the tunnel connects from another host. That is safe: every request except `/healthz` needs a valid Access assertion,
so one sent straight to the port from the LAN gets 403. Without Access settings it listens on 127.0.0.1 only and
refuses anything a proxy forwarded.

## 2. The Access application, with managed OAuth

**Access, Applications, Add an application, Self-hosted**:

- Domain: `hometown.example.com`, path `hometown/mcp` (a separate application from the app's own, so it has its own
  audience tag).
- Policy: allow the editors' emails (the same people as `ADMIN_EMAILS`).
- Turn on **Managed OAuth** for the application. Access then acts as the OAuth server for Claude's connector: Claude
  signs in through your usual Access login, and every request Access lets through reaches the service with a signed
  assertion. The service never runs its own OAuth and answers a request without a valid assertion with 403.
- Copy the application's **Audience (AUD) tag**.

Then set the MCP's settings:

```
vim /etc/conf.d/hometown
```

| Setting | Value |
|---|---|
| `CF_ACCESS_TEAM_DOMAIN` | `<team>.cloudflareaccess.com` |
| `CF_ACCESS_AUD` | the MCP application's AUD tag (not the app's `CF_AUD`) |
| `MCP_PUBLIC_HOSTS` | `hometown.example.com` (the Host the tunnel passes through; anything else gets 421) |
| `MCP_ALLOWED_EMAILS` | optional: only these emails, even if Access lets others through |
| `HOMETOWN_MCP_WRITES` | `none`, `log` or `all` (below) |

`MCP_HTTP_PORT` (8783), `MCP_HTTP_PATH` (`/hometown/mcp`) and `HOMETOWN_URL` (`http://127.0.0.1:8782`) are already set
by the installer. The first time, re-run the installer so it starts `hometown-mcp` and adds its health check; after
that, `rc-service hometown-mcp restart` picks up a change. Check it with:

```
wget -qO- http://127.0.0.1:8783/healthz        # {"status": "ok"} while the app answers
tail /var/log/hometown-mcp.log
```

## 3. The connector in claude.ai

In claude.ai, **Settings, Connectors, Add custom connector**: name it Hometown Table, URL
`https://hometown.example.com/hometown/mcp`, and connect. The Access sign-in opens; sign in as one of the editors.
Claude Desktop and Claude Code signed in to the same claude.ai account use the same connector, so there is nothing to
set up on each computer, and no key ever leaves the container.

**"Me".** Claude acts as whoever signed in to Access: the service forwards that email to the app, which honours it only
for an email in `ADMIN_EMAILS`. For "log that I went to Casa Ejemplo" or "where haven't I been lately", link each
editor's email to their person in the app's **Settings**. An editor who isn't linked gets "Link your email to a person
in Settings." and nothing is saved.

## 4. Write levels

`HOMETOWN_MCP_WRITES` decides which tools Claude sees. Unknown values count as `none` (with a warning in the log).

| Level | Tools |
|---|---|
| `none` (default) | `suggest_places`, `search_places`, `get_place`, `visit_history`, `list_inbox`, `list_people`, `list_tags`, `list_lists` |
| `log` | the above, plus `log_visit` and `set_want_to_try` |
| `all` | the above, plus `import_places`, `sort_candidate`, `set_ownership` and `set_standing` |

At `none` the service holds only `HOMETOWN_READ_KEY`, which the app lets read and nothing else. It refuses to start if
it can see `HOMETOWN_API_KEY`; `/etc/init.d/hometown-mcp` passes that key on only at `log` and `all`. To change the
level, `vim /etc/conf.d/hometown`, then `rc-service hometown-mcp restart`.

## 5. Rotating a key

Both keys live in `/etc/conf.d/hometown` and may hold several comma-separated values; the app accepts any of them and
the MCP service uses the first. To replace one without a gap:

```
head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; echo     # a new key
vim /etc/conf.d/hometown                                       # HOMETOWN_API_KEY="<new>,<old>"
rc-service hometown restart; rc-service hometown-mcp restart
vim /etc/conf.d/hometown                                       # HOMETOWN_API_KEY="<new>"
rc-service hometown restart
```

The same goes for `HOMETOWN_READ_KEY`. Never paste a key into a chat, a ticket or the repo.

## 6. Stdio, for development

`mcp_server.py` also runs over stdio, against a local dev server (or on the container itself, where loopback keys
work). With the app running (`python3 app.py`, keys from your `.env`) and the MCP packages installed
(`pip install -r requirements-mcp.txt`):

```
HOMETOWN_MCP_WRITES=log HOMETOWN_API_KEY=<your dev write key> python3 mcp_server.py --as alex@example.com
```

`HOMETOWN_URL` defaults to `http://127.0.0.1:8782`. The key rules are the service's: `HOMETOWN_READ_KEY` at `none`
(and no `HOMETOWN_API_KEY` in the environment), `HOMETOWN_API_KEY` at `log` and `all`. `--as` is the editor "me" means
(it must be in the app's `ADMIN_EMAILS`). To use it from Claude Code, add it as a stdio server with those variables in
its `env`. There is no LAN or workstation key path: from another computer, use the connector.

## Troubleshooting

- **403 `{"error": "forbidden"}`**: the request had no valid Access assertion. It didn't come through the tunnel route,
  the AUD tag is the app's instead of the MCP application's, or the email isn't in `MCP_ALLOWED_EMAILS`. The log says
  which.
- **421 Invalid Host header**: `MCP_PUBLIC_HOSTS` doesn't name the hostname the tunnel sends.
- **"Hometown Table refused this service's key"**: the key in the service's environment isn't one the app accepts.
  Restart both services after editing the keys.
- **"Hometown Table is unreachable right now"**: the app is down; `rc-service hometown status`.
- **The service won't start**: `tail /var/log/hometown-mcp.log`. It names the setting it refuses (the write key at
  `none`, an empty key, Access settings missing for a public hostname).
