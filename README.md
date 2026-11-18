# FSG match archive

Every Goondesliga, Spoondesliga and Kzar's Kzup match, pulled automatically from Xpert Eleven every 3 hours and saved here for the FSG viewer.

- `index.json`: every archived match (teams, score, season, round, competition, file path), newest first
- `matches/S44/R06/<gameId>.json`: the full match data for each game

## One-time setup (all on github.com, no command line)

1. **Create the repo**
   - github.com → **+** (top right) → **New repository**
   - Name it `fsg-data`, set it to **Public** (so the game can read it), and click **Create repository**.

2. **Upload the files**
   - In the new repo, click **uploading an existing file**.
   - Unzip `fsg-data.zip` on your computer and drag **everything inside the folder** into the page, including the `.github` folder. If your computer hides the `.github` folder, see the note at the bottom.
   - Click **Commit changes**.

3. **Add your secrets** (the bot's settings, kept private)
   - Repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**. Add each one:

   | Name | Value |
   |---|---|
   | `X11_USERNAME` | your X11 username |
   | `X11_PASSWORD` | your X11 password |
   | `GOONDESLIGA_URL` | the Goondesliga league page URL (the same one the Taycan bot used) |
   | `SPOONDESLIGA_URL` | the Spoondesliga league page URL |
   | `KZUP_URL` | optional: the Kzar's Kzup page URL |
   | `NTFY_TOPIC` | optional: an ntfy.sh topic name, to get a phone alert if fetching breaks |

4. **Run it once to test**
   - Repo → **Actions** tab → if asked, click **I understand my workflows, go ahead and enable them**.
   - Click **Fetch X11 matches** → **Run workflow** → **Run workflow**.
   - Click on the run to watch it. A green tick means it worked. New match files appear in `matches/` and `index.json` updates.
   - If it goes red, open the run, copy the log text, and send it to Claude.

From then on it runs by itself every 3 hours.

## Grabbing old matches

Actions → **Fetch X11 matches** → **Run workflow**, then paste game IDs into the box, separated by commas (e.g. `323904616, 323904604`). The game ID is the `gameID=` number in a match link.

## Links for the game

- Index: `https://raw.githubusercontent.com/<your-username>/fsg-data/main/index.json`
- A match: `https://raw.githubusercontent.com/<your-username>/fsg-data/main/<file from index.json>`

## Note on the hidden `.github` folder

If the upload page doesn't get `.github/workflows/fetch.yml`, create it by hand instead: in the repo, **Add file** → **Create new file**, type the name `.github/workflows/fetch.yml` (the slashes create the folders), paste in the contents of `fetch.yml`, and click **Commit changes**.
