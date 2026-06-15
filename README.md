# img_content_curation

Pipeline CLI per curare contenuti immagine hotel a partire da un CSV ICEPortal. Il workflow principale e' `pipeline.py`: seleziona gli hotel da processare, analizza le immagini con Gemini, genera classificazione amenity e testi editoriali, salva un CSV per hotel e mantiene un CSV cumulativo pronto per l'export.

## Cosa fa

Per ogni immagine selezionata la pipeline:

- scarica l'immagine da `Asset_Link`;
- invia l'immagine a Gemini come `inline_data`;
- classifica l'immagine rispetto alla taxonomy amenity configurata in `prompts.yaml`;
- genera caption experience, description experience, alt text e flag `Check_Room`;
- scrive un CSV dedicato all'hotel;
- aggiorna `output_hotels/all_hotels_cumulative.csv`;
- salva log JSONL e checkpoint temporanei per riprendere lavorazioni interrotte.

## File principali

- `pipeline.py`: pipeline principale di content curation.
- `prompts.yaml`: modello, tone of voice, regole di generazione, costi stimati e taxonomy amenity.
- `export_content_excel.py`: esporta il CSV cumulativo in formato Excel `.xlsx`.
- `requirements.txt`: dipendenze Python.
- `.env.example`: template per la chiave Gemini.

## Requisiti

- Python 3.11 consigliato.
- Chiave API Gemini disponibile in ambiente.
- CSV input separato da `;`.

Installazione dipendenze:

```bash
pip install -r requirements.txt
```

Configurazione chiave API:

```bash
cp .env.example .env
```

Poi valorizza `GEMINI_API_KEY` nel file `.env` oppure esportala nella shell:

```bash
export GEMINI_API_KEY="..."
```

Su PowerShell:

```powershell
$env:GEMINI_API_KEY="..."
```

## Input CSV

Il CSV sorgente deve essere separato da `;` e deve contenere almeno queste colonne:

- `Listing_MappedID`
- `Listing_Name`
- `Asset_Link`

La pipeline usa anche altre colonne quando presenti, per migliorare prompt, output ed export:

- `Listing_ICEID`
- `Listing_Brand`
- `Asset_Index`
- `Asset_PublicID`
- `Asset_FileID`
- `Asset_Caption`
- `Asset_MediaType`

`Asset_FileID` e' importante per i checkpoint: se presente, permette di non riprocessare immagini gia' completate dopo un'interruzione.

## Uso base

Processare uno o piu' hotel per `Propid`:

```bash
python pipeline.py \
  --input input.csv \
  --output-dir output_hotels \
  --prompts prompts.yaml \
  --propid 77519,98373
```

Processare un hotel per nome esatto:

```bash
python pipeline.py \
  --input input.csv \
  --hotel-name "Zante Park Resort & Spa, BEST WESTERN Premier Collection"
```

Processare hotel da un file di nomi, uno per riga:

```bash
python pipeline.py \
  --input input.csv \
  --hotel-name-file hotel_names.txt
```

Processare i prossimi 3 hotel senza output gia' presente:

```bash
python pipeline.py \
  --input input.csv \
  --next-hotels 3
```

Rigenerare anche file hotel gia' esistenti:

```bash
python pipeline.py \
  --input input.csv \
  --propid 77519 \
  --force
```

## Opzioni utili

- `--output-dir`: directory degli output. Default: `output_hotels`.
- `--prompts`: file YAML o JSON di configurazione prompt. Default: `prompts.yaml`.
- `--model`: override del modello configurato in `prompts.yaml`.
- `--workers`: numero di thread paralleli per le immagini di un hotel. Default: `5`.
- `--timeout`: timeout in secondi per download immagini e chiamate API. Default: `60`.
- `--max-retries`: retry per download e chiamate API. Default: `3`.
- `--request-delay`: pausa opzionale tra immagini processate.
- `--log-dir`: directory dei log JSONL. Default: `logs`.
- `--debug-log`: include dettagli diagnostici su retry, parsing e risposte Gemini.
- `--dry-run`: stima immagini, chiamate API, costo e tempo senza processare immagini.

Esempio dry run:

```bash
python pipeline.py \
  --input input.csv \
  --next-hotels 10 \
  --workers 5 \
  --dry-run
```

## Output pipeline

Per ogni hotel viene creato un file:

```text
output_hotels/<Propid>_<hotel-name-slug>.csv
```

In piu', la pipeline aggiorna:

```text
output_hotels/all_hotels_cumulative.csv
```

Il CSV hotel e il cumulativo mantengono le colonne originali e aggiungono campi di content curation:

- `Amenity_Category`
- `Amenity_Codes`
- `Amenity_MaxCategory`
- `Amenity_CustomTag1`
- `Amenity_CustomTag2`
- `Amenity_CustomTag3`
- `Amenity_CustomTag4`
- `Amenity_CustomTags`
- `Caption_Experience`
- `Description_Experience`
- `Alt_Text`
- `Check_Room`

`Check_Room` vale `1` solo quando l'immagine mostra chiaramente una camera hotel o un bagno privato della camera; negli altri casi vale `0`.

## Checkpoint e log

Durante il processing di un hotel, la pipeline crea un file sidecar temporaneo:

```text
output_hotels/<hotel-file>.progress.jsonl
```

Questo file registra le righe gia' completate e consente di riprendere il lavoro senza ricominciare da zero. Quando l'hotel viene completato correttamente, il sidecar viene rimosso.

Ogni run crea inoltre un log JSONL in:

```text
logs/pipeline_<timestamp>.log
```

Il log contiene eventi di classificazione, generazione, errori, tempi e dettagli diagnostici se `--debug-log` e' attivo.

## Export Excel

Dopo aver generato o aggiornato `all_hotels_cumulative.csv`, crea l'Excel finale con:

```bash
python export_content_excel.py \
  --input output_hotels/all_hotels_cumulative.csv \
  --output output_hotels/content_export.xlsx
```

Senza argomenti, lo script usa questi default:

```text
input:  output_hotels/all_hotels_cumulative.csv
output: output_hotels/content_export.xlsx
```

Il file Excel contiene le colonne, in questo ordine:

```text
IceID, MappedID, Hotel, AssetType, Index, PublicID, Category, Custom Tags, Caption, Description, Alt Text, URL
```

Mappatura applicata:

- `IceID` <- `Listing_ICEID`
- `MappedID` <- `Listing_MappedID`
- `Hotel` <- `Listing_Brand` + `Listing_Name`
- `AssetType` <- `PH`
- `Index` <- `Asset_Index`
- `PublicID` <- `Asset_PublicID`
- `Category` <- `Amenity_Category`
- `Custom Tags` <- `Amenity_CustomTags`
- `Caption` <- `Caption`
- `Description` <- `Description`
- `Alt Text` <- `Alt_Text`
- `URL` <- `Asset_Link`

Nota: l'export legge le colonne `Caption` e `Description`. Se il CSV cumulativo contiene solo `Caption_Experience` e `Description_Experience`, bisogna normalizzare quei nomi colonna o aggiornare la mappatura in `export_content_excel.py` prima di generare l'Excel finale.

## Configurazione prompt

`prompts.yaml` controlla:

- modello Gemini;
- tone of voice;
- temperatura e token massimi per generazione;
- temperatura e schema risposta per classificazione;
- limiti di lunghezza;
- taxonomy amenity;
- stima costi per dry run.

La taxonomy amenity associa ogni categoria a codici e custom tag. La pipeline usa la categoria classificata da Gemini e la risolve nei campi `Amenity_*` finali. Se il punteggio e' sotto soglia o la categoria non e' riconosciuta, viene usata `Other`.

## Workflow consigliato

1. Aggiorna `prompts.yaml` se devi cambiare tone of voice, modello o taxonomy.
2. Esegui un dry run per stimare costo e durata.
3. Processa un singolo hotel con `--propid` per validare output e prompt.
4. Processa batch piu' grandi con `--next-hotels` o `--hotel-name-file`.
5. Controlla `output_hotels/all_hotels_cumulative.csv`.
6. Genera l'Excel finale con `export_content_excel.py`.

## Troubleshooting

Se manca la chiave API:

```text
Missing Gemini API key. Set --api-key or GEMINI_API_KEY.
```

Configura `.env` o esporta `GEMINI_API_KEY` nella shell.

Se un hotel e' gia' stato processato, la pipeline lo salta. Usa `--force` per rigenerarlo.

Se una riga immagine fallisce, la pipeline continua sulle altre righe e registra l'errore nel log. I campi generati per quella riga restano vuoti o vengono valorizzati con fallback dove previsto.

Se ricevi molti errori `429`, riduci `--workers`, aumenta `--request-delay` o riprova piu' tardi.

## Test

Esegui i test con:

```bash
python -m unittest
```
