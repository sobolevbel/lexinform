# RCL through a proxy

`legislacja.rcl.gov.pl` drops TCP connections from GitHub-hosted runners (Azure, US): the SYN is
never answered, for every path and User-Agent, while the Sejm API answers from the same runner
(verified 2026-09-09 with `.github/workflows/rcl-probe.yml`). From an EU address, even a Hetzner
one in Helsinki, the site answers in 0.3 s. So the bot reaches RCL through a small HTTP forward
proxy on an EU host, configured with `LEXINFORM_RCL_PROXY_URL`
(`http://user:password@host:port`); only the RCL client uses it, the Sejm API, the LLM and
Telegram connect directly.

## The proxy host

Anything in the EU with one open TCP port works. Used so far: a free Mikrus FROG server
(Alpine in an unprivileged LXC, Hetzner Helsinki, shared IPv4 with three forwarded ports
`frog01.mikr.us:2xxxx/3xxxx/4xxxx`, `sudo` without password). A FROG server is deleted after
three months without an SSH login; the paid Mikrus 1.0 (35 zł a year, same location) has no such
rule.

## tinyproxy, restricted to RCL

Install and configure as root (replace `PORT` with one of the forwarded ports and `PASSWORD` with
`openssl rand -hex 16`):

```sh
sudo apk add tinyproxy
sudo tee /etc/tinyproxy/tinyproxy.conf >/dev/null <<'EOF'
User tinyproxy
Group tinyproxy
Port PORT
Listen 0.0.0.0
Timeout 120
MaxClients 20
LogLevel Warning
LogFile "/var/log/tinyproxy/tinyproxy.log"
PidFile "/run/tinyproxy/tinyproxy.pid"
DisableViaHeader Yes
BasicAuth lexinform PASSWORD
ConnectPort 443
Filter "/etc/tinyproxy/filter"
FilterDefaultDeny Yes
FilterExtended On
Allow 0.0.0.0/0
Allow ::/0
EOF
printf '^legislacja\\.rcl\\.gov\\.pl$\n' | sudo tee /etc/tinyproxy/filter >/dev/null
sudo mkdir -p /var/log/tinyproxy /run/tinyproxy
sudo chown tinyproxy:tinyproxy /var/log/tinyproxy /run/tinyproxy
sudo rc-update add tinyproxy default
sudo rc-service tinyproxy restart
ss -ltn | grep PORT
```

What the configuration guarantees: credentials are required (`BasicAuth`), only `CONNECT` to
port 443 is tunnelled, and only the RCL host passes the filter (`FilterDefaultDeny`), so a leaked
password cannot turn the box into an open proxy. The Via header is off so RCL sees a plain client.

## Check and wire it in

From any machine:

```sh
curl -o /dev/null -w "%{http_code} %{time_total}s\n" -x "http://lexinform:PASSWORD@HOST:PORT" \
  'https://legislacja.rcl.gov.pl/lista?typeId=2&sKey=modifiedDate&sOrder=desc&pSize=10&pNumber=1'   # 200
curl -o /dev/null -w "%{http_code}\n" -x "http://lexinform:PASSWORD@HOST:PORT" https://api.sejm.gov.pl/sejm/term  # 403
```

Then store the URL as the repository secret `LEXINFORM_RCL_PROXY_URL`
(`gh secret set LEXINFORM_RCL_PROXY_URL`), which `daily.yml` passes to the run and
`rcl-probe.yml` uses for its last step. Locally, put the same line into `.env`.
