# Reminders

## 🗂️ Go Module Cache
Go caches every module version permanently — grows fast (hit ~1 GB by April 2026).
Safe to clean anytime: `go clean -modcache`
Re-downloads automatically on next `go run` / `go build`.
**Check monthly:** `du -sh /root/go`
