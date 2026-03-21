# IPRoyal Static Residential Proxy — Quick Research

## 1. Pricing (Monthly)

| Duration | Price/IP | Notes |
|----------|----------|-------|
| 24 hours | $1.80 | Test option |
| 30 days | $2.70 | Monthly — most popular |
| 60 days | $2.55 | |
| 90 days | $2.40 | |

- Per-IP billing, **unlimited traffic**
- Supports SOCKS5 + HTTP(S)
- Bulk/enterprise custom plans available
- IPs from 31+ countries

## 2. SOCKS5 Proxy URL

After purchase, dashboard → proxy details → select **SOCKS5** protocol.

**Connection format:**
```
socks5://username:password@host:port
```

- **Host:** proxy IP or `geo.iproyal.com`
- **Default SOCKS5 port:** `12324` (customizable)
- Username/password set in dashboard
- IP whitelisting also available (auth-less)

Dashboard auto-generates formatted proxy strings. You can customize credentials.

## 3. Country Selection

**Yes — you can choose specific countries.** 31+ countries available. In the dashboard, pick your target country/geo-location when configuring the proxy. Static IPs are assigned from that country.

For rotating countries, update location in dashboard → regenerate proxy details. Static IPs remain sticky per session.

## 4. Sign-Up & API

**Sign-up:** Manual via [iproyal.com](https://iproyal.com) dashboard. Buy static residential proxies there.

**API:** Available for managing existing proxies, **NOT for purchasing**.

Key API endpoints:
- **Auth:** `X-Access-Token: <token>` header (from dashboard Settings → API)
- **Check availability by country:**
  ```
  GET https://apid.iproyal.com/v1/reseller/access/availability/static-residential
  ```
  Returns available IPs per country.
- **Get entry nodes:** Returns IPs/ports/DNS for connections
- Base URL: `https://apid.iproyal.com/v1/reseller`

**No purchase/provisioning API found.** Must buy via dashboard. Support: support@iproyal.com

## Summary

- **Cost:** ~$2.70/mo per IP (unlimited bandwidth)
- **SOCKS5:** Supported, standard `socks5://user:pass@host:port` format
- **Countries:** 31+ available, non-US options exist
- **Programmatic:** API for management only, manual purchase via dashboard
