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

## Export Excel

Dopo aver generato o aggiornato il CSV cumulativo della pipeline completa, esporta il file Excel finale con:

```bash
python export_content_excel.py \
  --input output_hotels/all_hotels_cumulative.csv \
  --output output_hotels/content_export.xlsx
```

Il file Excel contiene queste colonne, in questo ordine:

`IceID`, `MappedID`, `Hotel`, `AssetType`, `Index`, `PublicID`, `Category`, `Custom Tags`, `Caption`, `Description`, `Alt Text`, `URL`.

Mappatura applicata rispetto al CSV esportato:

- `IceID` <- `Listing_ICEID`
- `MappedID` <- `Listing_MappedID`
- `Hotel` <- concatenazione di `Listing_Brand` e `Listing_Name`
- `AssetType` <- `PH` per tutte le righe
- `Index` <- `Asset_Index`
- `PublicID` <- `Asset_PublicID`
- `Category` <- `Amenity_Category`
- `Custom Tags` <- `Amenity_CustomTags`
- `Caption` <- `Caption`
- `Description` <- `Description`
- `Alt Text` <- `Alt_Text`
- `URL` <- `Asset_Link`
