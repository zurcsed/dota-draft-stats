# Dota draft helper — shared hero stats

Every 6 hours a GitHub Actions job downloads hero statistics from the STRATZ API
(all ranks, ranked games, last 4 complete weeks plus the running one): matchups, games per
position and lane partners per position. It publishes them as one file on GitHub Pages:
`stats.json`. The Dota draft helper app downloads that file, so its users need no STRATZ account.

The app itself: [Draft Helper.exe](https://github.com/zurcsed/dota-draft-stats/raw/main/Draft%20Helper.exe)
(Windows, one file, nothing to install).

Setup: repository secret `STRATZ_TOKEN` (a STRATZ API token), Settings → Pages → Source:
"GitHub Actions". Manual run: Actions → "Update hero stats" → "Run workflow".
