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
python pipeline.py --input "Download_ICEPortal_ID img(Sheet1).csv" --propid 77519
```

More than one hotel by `Propid`:

```bash
python pipeline.py --input "Download_ICEPortal_ID img(Sheet1).csv" --propid 77519,98373
```

Hotel by exact name:

```bash
python pipeline.py --input "Download_ICEPortal_ID img(Sheet1).csv" --hotel-name "Zante Park Resort & Spa, BEST WESTERN Premier Collection"
```

More than one hotel name:

```bash
python pipeline.py --input "Download_ICEPortal_ID img(Sheet1).csv" --hotel-name "Zante Park Resort & Spa, BEST WESTERN Premier Collection" --hotel-name "BEST WESTERN Hotel Adige"
```

Next unprocessed hotels:

```bash
python pipeline.py --input "Download_ICEPortal_ID img(Sheet1).csv" --next-hotels 3
```

Force regeneration:

```bash
python pipeline.py --input "Download_ICEPortal_ID img(Sheet1).csv" --propid 77519 --force
```

Custom output directory and prompts file:

```bash
python pipeline.py --input "Download_ICEPortal_ID img(Sheet1).csv" --output-dir output_hotels --prompts prompts.yaml --propid 77519
```

## Output

The script writes one file per hotel in `output_hotels/` using this pattern:

```text
<Propid>_<hotel-name-slug>.csv
```

Each output CSV keeps all original columns and appends:

- `AI_Amenity_Category`   (Altro if no category exceeds score 0.4)
- `AI_Amenity_Codes`
- `AI_Amenity_Maxcategoria`
- `AI_Amenity_CustomTag1`
- `AI_Amenity_CustomTag2`
- `AI_Amenity_CustomTag3`
- `AI_Amenity_CustomTag4`
- `AI_Caption_Basic`
- `AI_Description_Basic`
- `AI_Caption_Experience`
- `AI_Description_Experience`
- `AI_Image_Tag`
- `AI_Alt_Text`

## Dry-run

```bash
python pipeline.py --input "file.csv" --next-hotels 5 --dry-run
```

Prints an estimate of images, API calls, and cost per hotel and total. It does not download or process any image.

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

## Notes

- If an image or API call fails, the row is still written and the AI columns remain empty for that image.
- Existing hotel files are skipped unless you pass `--force`.
- Name-based selection must match the CSV exactly after whitespace normalization.
- `output_hotels/`, `.env`, logs, and checkpoint sidecars are ignored by Git.
- The checkpoint saves progress image by image in a `.progress.jsonl` file in the same directory as the output CSV.
- If the process is interrupted, rerunning the same command resumes from the last unprocessed image.
- The sidecar is deleted automatically when the hotel completes successfully.
- If you want to restart a partially processed hotel from scratch, delete the corresponding `.progress.jsonl` file manually.
