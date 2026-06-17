# img_content_curation

Pipeline CLI per arricchire immagini hotel da un CSV ICEPortal e preparare un export Excel finale per content curation.

Il progetto usa una sola pipeline principale:

- `pipeline.py` legge il CSV sorgente, processa le immagini per hotel con Gemini Vision e produce CSV arricchiti.
- `export_content_excel.py` converte il cumulativo della pipeline in `content_export.xlsx` con le colonne finali richieste.

Non serve `pipeline_hotels.py`: tutta la logica di selezione hotel, resume, output per hotel e cumulativo e' dentro `pipeline.py`.

## Struttura

```text
.
├── pipeline.py                 # Pipeline principale per classificazione e generazione contenuti
├── export_content_excel.py     # Export Excel finale da all_hotels_cumulative.csv
├── prompts.yaml                # Prompt, modello, tone of voice, taxonomy e stime costo
├── requirements.txt            # Dipendenze Python minime
├── test_export_content_excel.py # Test unitari per export Excel
├── .env.example                # Template variabili ambiente
└── .devcontainer/              # Setup GitHub Codespaces
```

## Setup

### GitHub Codespaces

1. Apri il repository in Codespaces.
2. Aggiungi un secret Codespaces chiamato `GEMINI_API_KEY`.
3. Carica il CSV sorgente nel workspace.
4. Esegui la pipeline dal terminale.

Il dev container installa Python 3.11 e le dipendenze in `requirements.txt`.

### Setup locale

```bash
pip install -r requirements.txt
cp .env.example .env
```

Poi inserisci la chiave reale in `.env`:

```text
GEMINI_API_KEY=...
```

`pipeline.py` carica automaticamente `.env` dalla root del progetto.

## Input CSV

Il CSV sorgente deve essere separato da `;`.

Colonne minime richieste dalla pipeline:

- `Listing_MappedID`
- `Listing_Name`
- `Asset_Link`

Colonne usate quando presenti per migliorare prompt, output e naming:

- `Listing_ICEID`
- `Listing_Brand`
- `Asset_FileID`
- `Asset_Index`
- `Asset_PublicID`
- `Asset_Caption`
- `Asset_MediaType`

## Eseguire la pipeline

### Singolo hotel per Propid

```bash
python pipeline.py --input data.csv --propid 77519
```

### Piu hotel per Propid

```bash
python pipeline.py --input data.csv --propid 77519,98373
```

### Hotel per nome esatto

```bash
python pipeline.py --input data.csv --hotel-name "Zante Park Resort & Spa, BEST WESTERN Premier Collection"
```

### Lista di hotel da file

```bash
python pipeline.py --input data.csv --hotel-name-file hotels.txt
```

Il file deve contenere un nome hotel per riga.

### Prossimi hotel non ancora processati

```bash
python pipeline.py --input data.csv --next-hotels 3
```

### Rigenerare output esistenti

```bash
python pipeline.py --input data.csv --propid 77519 --force
```

### Worker paralleli

```bash
python pipeline.py --input data.csv --next-hotels 10 --workers 15
```

Default: `--workers 5`.

Valore consigliato con Gemini Tier 1: `--workers 15`.

Evita di superare 20 worker: rate limit Gemini e download immagini diventano il collo di bottiglia.

### Dry run

```bash
python pipeline.py --input data.csv --next-hotels 5 --workers 15 --dry-run
```

Il dry run stampa stima immagini, chiamate API, tempo e costo. Non scarica immagini e non chiama Gemini.

## Output pipeline

La pipeline scrive un CSV per hotel dentro `output_hotels/`:

```text
output_hotels/<Propid>_<hotel-name-slug>.csv
```

Ogni CSV mantiene tutte le colonne originali e aggiunge:

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

La pipeline mantiene anche un file cumulativo persistente:

```text
output_hotels/all_hotels_cumulative.csv
```

Quando un hotel viene processato di nuovo, le righe di quell'hotel nel cumulativo vengono sostituite con la versione piu recente.

## Resume e checkpoint

Durante il processamento di un hotel, la pipeline salva un sidecar di progresso:

```text
output_hotels/<Propid>_<hotel-name-slug>.progress.jsonl
```

Se il processo si interrompe, rilancia lo stesso comando: le immagini gia completate vengono recuperate dal checkpoint.

Quando l'hotel finisce correttamente, il sidecar viene eliminato automaticamente.

Per ripartire da zero su un hotel parzialmente processato, elimina manualmente il relativo `.progress.jsonl` oppure usa `--force` se l'output CSV esiste gia.

## Export Excel

Dopo aver generato o aggiornato il cumulativo, crea l'Excel finale con:

```bash
python export_content_excel.py
```

Default:

- input: `output_hotels/all_hotels_cumulative.csv`
- output: `output_hotels/content_export.xlsx`
- delimiter: `;`

Comando esplicito equivalente:

```bash
python export_content_excel.py --input output_hotels/all_hotels_cumulative.csv --output output_hotels/content_export.xlsx
```

L'Excel contiene solo queste colonne, in questo ordine:

1. `IceID`
2. `MappedID`
3. `Hotel`
4. `AssetType`
5. `Index`
6. `PublicID`
7. `Category`
8. `Custom Tags`
9. `Caption`
10. `Description`
11. `Alt Text`
12. `URL`

Mappatura export:

| Colonna Excel | Colonna sorgente |
| --- | --- |
| `IceID` | `Listing_ICEID` |
| `MappedID` | `Listing_MappedID` |
| `Hotel` | `Listing_Brand` + `Listing_Name` |
| `AssetType` | valore fisso `PH` |
| `Index` | `Asset_Index` |
| `PublicID` | `Asset_PublicID` |
| `Category` | `Amenity_Category` |
| `Custom Tags` | `Amenity_CustomTags` |
| `Caption` | `Caption_Experience` |
| `Description` | `Description_Experience` |
| `Alt Text` | `Alt_Text` |
| `URL` | `Asset_Link` |

Nota importante: l'export non usa eventuali colonne legacy `Caption` e `Description`; usa sempre `Caption_Experience` e `Description_Experience` prodotte dalla pipeline attuale.

Il file `.xlsx` viene generato con sola standard library Python (`csv`, `zipfile`, XML). Non servono `openpyxl`, `pandas` o altre librerie esterne.

La prima riga dell'Excel e' congelata e ha l'autofilter attivo.

## Logging

Ogni run crea un log JSONL in:

```text
logs/pipeline_YYYYMMDD_HHMMSS.log
```

Campi principali:

- `timestamp`
- `propid`
- `hotel_name`
- `asset_fileid`
- `asset_index`
- `asset_caption`
- `asset_link`
- `step`
- `ai_amenity_category`
- `ai_amenity_score`
- `duration_ms`
- `error`

Per diagnostica piu dettagliata:

```bash
python pipeline.py --input data.csv --propid 77519 --debug-log
```

Con `--debug-log` vengono tracciati anche retry, parse failure, checkpoint hit, tempi di attesa, status HTTP e snippet delle risposte Gemini quando utile.

## Test

Esegui i test dell'export Excel con:

```bash
python -m unittest test_export_content_excel.py
```

Verifica manuale completa consigliata:

```bash
python -m unittest test_export_content_excel.py
python pipeline.py --input data.csv --propid 77519
python export_content_excel.py --input output_hotels/all_hotels_cumulative.csv --output output_hotels/content_export.xlsx
```

## Troubleshooting

### Missing Gemini API key

Errore tipico:

```text
Missing Gemini API key. Set --api-key or GEMINI_API_KEY.
```

Soluzione: imposta `GEMINI_API_KEY` in `.env`, come secret Codespaces, oppure passa `--api-key`.

### Propid non trovato

Errore tipico:

```text
Propid not found in CSV: 77519
```

Controlla che il valore sia presente nella colonna `Listing_MappedID` e che il CSV sia quello corretto.

### Nome hotel non trovato o ambiguo

La selezione con `--hotel-name` richiede match esatto dopo normalizzazione degli spazi. Se piu hotel hanno lo stesso nome, usa `--propid`.

### Output gia esistente

Se il CSV hotel esiste gia, la pipeline lo salta e sincronizza comunque le righe nel cumulativo.

Per rigenerare:

```bash
python pipeline.py --input data.csv --propid 77519 --force
```

### Export Excel vuoto o incompleto

Controlla prima il cumulativo:

```bash
ls output_hotels/all_hotels_cumulative.csv
```

Poi rigenera l'Excel:

```bash
python export_content_excel.py
```

## File ignorati da Git

Normalmente non vanno versionati:

- `.env`
- `output_hotels/`
- `logs/`
- `*.progress.jsonl`
- export generati come `content_export.xlsx`
