# Dota draft helper — shared hero stats

Every 6 hours a GitHub Actions job downloads hero matchup statistics from the STRATZ API
(all ranks, ranked games, last 4 complete weeks plus the running one) and publishes them as one
file on GitHub Pages: `stats.json`. The Dota draft helper app downloads that file, so its users
need no STRATZ account.

Setup: repository secret `STRATZ_TOKEN` (a STRATZ API token), Settings → Pages → Source:
"GitHub Actions". Manual run: Actions → "Update hero stats" → "Run workflow".
