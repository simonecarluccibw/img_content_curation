# Hotel Image Caption Pipeline

Batch-oriented CLI that reads an ICEPortal CSV once, groups rows by hotel, and writes one enriched CSV per hotel with AI-generated captions, descriptions, alt text, and a forced tourism tag.

## Repository structure

- `pipeline.py`: main CLI
- `prompts.yaml`: editable tone of voice and prompt rules
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

Create a virtual environment if you want, then install dependencies:

```bash
pip install -r requirements.txt
```

Set your API key:

```bash
cp .env.example .env
```

Then export `GEMINI_API_KEY` in your shell or configure it in your IDE terminal session.

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

- `AI_Caption_Basic`
- `AI_Description_Basic`
- `AI_Caption_Experience`
- `AI_Description_Experience`
- `AI_Image_Tag`
- `AI_Alt_Text`

## Notes

- If an image or API call fails, the row is still written and the AI columns remain empty for that image.
- Existing hotel files are skipped unless you pass `--force`.
- Name-based selection must match the CSV exactly after whitespace normalization.
- `--hotel-name` values are not comma-split, so names containing commas work correctly.
- `output_hotels/`, `.env`, and temporary files are ignored by Git.
