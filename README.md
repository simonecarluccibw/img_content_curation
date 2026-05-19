# img_caption

Pipeline CLI per generare contenuti AI per immagini hotel, con output CSV separato per hotel.

## Requisiti

```bash
pip install requests pyyaml
```

Impostare la chiave API Gemini:

```bash
export GEMINI_API_KEY="..."
```

## Uso

```bash
python pipeline_hotels.py \
  --input input.csv \
  --output-dir output_hotels \
  --prompts prompts.yaml \
  --propid 77519,98373
```

Selezione alternativa:

```bash
python pipeline_hotels.py --input input.csv --hotel-name "Zante Park Resort & Spa, BEST WESTERN Premier Collection"
python pipeline_hotels.py --input input.csv --hotel-name-file hotel_names.txt
python pipeline_hotels.py --input input.csv --next-hotels 3
```

Opzioni principali:

- `--force`: rigenera i file hotel già esistenti.
- `--model`: override del modello (default: `gemini-2.5-flash`).

## Output

Per ogni hotel selezionato viene creato:

`<Propid>_<hotel-name-slug>.csv`

con delimitatore `;`, tutte le colonne originali + colonne AI:

- `AI_Caption_Basic`
- `AI_Description_Basic`
- `AI_Caption_Experience`
- `AI_Description_Experience`
- `AI_Image_Tag`
- `AI_Alt_Text`
