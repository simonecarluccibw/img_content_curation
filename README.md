# Hotel Image Caption Pipeline

Batch-oriented CLI that reads an ICEPortal CSV once, groups rows by hotel, runs two Gemini Vision calls per image, and writes one enriched CSV per hotel plus a per-run JSONL log.

## Repository structure

- `pipeline.py`: main CLI
- `prompts.yaml`: editable tone of voice, generation settings, classification settings, and amenity taxonomy
- `.devcontainer/devcontainer.json`: GitHub Codespaces setup
- `.env.example`: local environment template

## GitHub and Codespaces

This repository is ready for GitHub Codespaces.

### Recommended setup

1. Push the repository to GitHub.
2. Open it in a new Codespace.
3. Add a Codespaces secret named `GEMINI_API_KEY`.
4. Upload your CSV into the workspace, or keep it in the repo only if you want the data versioned.
5. Run the pipeline from the Codespaces terminal.

The dev container installs Python 3.11 and `requirements.txt` automatically.

## Local setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Set your API key:

```bash
cp .env.example .env
```

Then put your real key inside `.env`. The CLI automatically loads `.env` from the project root before parsing arguments.

## Input expectations

- Input CSV must be `;`-separated
- Required columns:
  - `Listing_MappedID`
  - `Listing_Name`
  - `Asset_Link`

## Example commands

Single hotel by `Propid`:

```bash
python pipeline.py --input "data.csv" --propid 77519
```

More than one hotel by `Propid`:

```bash
python pipeline.py --input "data.csv" --propid 77519,98373
```

Hotel by exact name:

```bash
python pipeline.py --input "data.csv" --hotel-name "Zante Park Resort & Spa, BEST WESTERN Premier Collection"
```

More than one hotel name:

```bash
python pipeline.py --input "data.csv" --hotel-name "Zante Park Resort & Spa, BEST WESTERN Premier Collection" --hotel-name "BEST WESTERN Hotel Adige"
```

Next unprocessed hotels:

```bash
python pipeline.py --input "data.csv" --next-hotels 3
```

Force regeneration:

```bash
python pipeline.py --input "data.csv" --propid 77519 --force
```

Custom output directory and prompts file:

```bash
python pipeline.py --input "data.csv" --output-dir output_hotels --prompts prompts.yaml --propid 77519
```

## Output

The script writes one file per hotel in `output_hotels/` using this pattern:

```text
<Propid>_<hotel-name-slug>.csv
```

Each output CSV keeps all original columns and appends:

- `Amenity_Category`   (`Other` if no category exceeds score 0.4)
- `Amenity_Codes`
- `Amenity_MaxCategory`
- `Amenity_CustomTag1`
- `Amenity_CustomTag2`
- `Amenity_CustomTag3`
- `Amenity_CustomTag4`
- `Amenity_CustomTags` (comma-separated union of the four custom tag columns)
- `Caption_Experience`
- `Description_Experience`
- `Alt_Text`
- `Check_Room` (`1` if the image clearly shows a hotel guest room or its private bathroom, otherwise `0`)

The pipeline also maintains a cumulative file:

```text
output_hotels/all_hotels_cumulative.csv
```

This file is persistent across runs. When a hotel is processed again, all of its rows in the cumulative file are replaced by the latest version.

## Dry-run

```bash
python pipeline.py --input "file.csv" --next-hotels 5 --dry-run
```

Prints an estimate of images, API calls, and cost per hotel and total. It does not download or process any image.

## Performance

- Conservative default: `--workers 5` for about 7 hours total runtime.
- Recommended with Gemini Tier 1: `--workers 15` for about 2.5 hours total runtime.
- Do not exceed 20 workers: Gemini rate limits (1,000 RPM) and image downloads become the bottleneck before CPU.
- Output CSV row order always matches the source CSV, regardless of thread completion order.

Example:

```bash
python pipeline.py --input data.csv --next-hotels 10 --workers 15
```

Expected end-to-end concurrent workflow:

```bash
# Estimate with 15 workers
python pipeline.py --input data.csv --next-hotels 10 --workers 15 --dry-run

# Process with 15 workers
python pipeline.py --input data.csv --next-hotels 10 --workers 15

# Resume after interruption with the same command
python pipeline.py --input data.csv --next-hotels 10 --workers 15
```

## Logging

Each run creates `logs/pipeline_YYYYMMDD_HHMMSS.log` in JSONL format. There is one row per image step.

Logged fields include:

- `timestamp`
- `propid`
- `hotel_name`
- `asset_fileid`
- `asset_index`
- `asset_caption`
- `asset_link`
- `step` (`classification` / `generation` / `error`)
- `ai_amenity_score`
- `duration_ms`
- `error`

For deeper diagnostics, enable:

```bash
python pipeline.py --input "file.csv" --propid 77519 --debug-log
```

With `--debug-log`, the JSONL file also captures retry attempts, parse failures, checkpoint hits, wait times, HTTP status codes, and short excerpts of raw Gemini responses when formatting breaks.

## Notes

- If an image or API call fails, the row is still written and the generated metadata columns remain empty for that image.
- Existing hotel files are skipped unless you pass `--force`.
- Name-based selection must match the CSV exactly after whitespace normalization.
- `output_hotels/`, `.env`, logs, and checkpoint sidecars are ignored by Git.
- The checkpoint saves progress image by image in a `.progress.jsonl` file in the same directory as the output CSV.
- If the process is interrupted, rerunning the same command resumes from the last unprocessed image.
- The sidecar is deleted automatically when the hotel completes successfully.
- If you want to restart a partially processed hotel from scratch, delete the corresponding `.progress.jsonl` file manually.
- The cumulative CSV is updated after each hotel completes successfully, and `--force` fully realigns that hotel's rows with the newest run.
- If a hotel CSV already exists and is skipped, its rows are still synchronized into the cumulative CSV.
