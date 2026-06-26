# img_content_curation

Pipeline CLI per classificare immagini hotel, generare copy esperienziale e produrre un export Excel finale per content curation.

Il progetto ha due script principali:

- `pipeline.py`: legge il CSV sorgente, processa le immagini per hotel, classifica le amenity e genera i contenuti.
- `export_content_excel.py`: converte il cumulativo CSV della pipeline in `output_hotels/content_export.xlsx`.

Non esiste piu una pipeline separata per hotel: selezione hotel, resume, checkpoint, output per hotel, cumulativo e sincronizzazione sono gestiti da `pipeline.py`.

## Struttura Del Progetto

```text
.
|-- pipeline.py                         # Pipeline principale
|-- export_content_excel.py             # Export Excel finale
|-- prompts.yaml                        # Modelli, prompt, provider, tone of voice e taxonomy
|-- requirements.txt                    # Dipendenze Python
|-- test_export_content_excel.py        # Test export Excel
|-- test_pipeline_content_generation.py # Test provider, tag e skip logiche content
|-- .env.example                        # Template variabili ambiente
`-- .devcontainer/                      # Setup GitHub Codespaces
```

Output generati normalmente non vanno versionati:

```text
.env
logs/
output_hotels/
*.progress.jsonl
*.xlsx
```

## Setup

### Codespaces

1. Apri il repository in GitHub Codespaces.
2. Configura almeno `GEMINI_API_KEY` nei secret Codespaces o nel file `.env`.
3. Configura `OPENROUTER_API_KEY` solo se vuoi usare OpenRouter per la generazione testi.
4. Carica il CSV sorgente, ad esempio `data.csv`.
5. Esegui la pipeline dal terminale.

Il dev container installa Python 3.11 e le dipendenze da `requirements.txt`.

### Setup Locale

```bash
pip install -r requirements.txt
cp .env.example .env
```

Poi modifica `.env`:

```env
GEMINI_API_KEY=...
OPENROUTER_API_KEY=...
```

`GEMINI_API_KEY` serve sempre, perche la classificazione amenity usa Gemini.

`OPENROUTER_API_KEY` serve solo se in `prompts.yaml` imposti:

```yaml
content_generation:
  provider: openrouter
```

## Come Funziona La Pipeline

Per ogni immagine la pipeline esegue questi passaggi:

1. Scarica l'immagine da `Asset_Link`.
2. Classifica l'immagine in una amenity usando Gemini Vision e la taxonomy in `prompts.yaml`.
3. Se la categoria e valida, genera `Caption_Experience`, `Description_Experience`, `Alt_Text` e `Check_Room` usando il provider configurato.
4. Se la categoria e `Other`, la riga resta nel CSV ma i campi content vengono lasciati vuoti e non viene fatta la generazione testi.
5. Scrive il CSV dell'hotel e aggiorna `output_hotels/all_hotels_cumulative.csv`.
6. Dopo la generazione dell'hotel, rinumera automaticamente i custom tag che finiscono con `-N`.

## Input CSV

Il CSV sorgente deve usare `;` come delimitatore. La pipeline accetta sia il formato
nativo con colonne `Listing_*` / `Asset_*`, sia il formato ICE/export come
`data-world2.csv`.

Colonne minime formato nativo:

- `Listing_MappedID`
- `Listing_Name`
- `Asset_Link`

Colonne consigliate, usate per prompt, export o naming:

- `Listing_ICEID`
- `Listing_Brand`
- `Asset_FileID`
- `Asset_Index`
- `Asset_PublicID`
- `Asset_Caption`
- `Asset_MediaType`

Colonne equivalenti supportate nel formato ICE/export:

| Formato ICE/export | Formato pipeline |
| --- | --- |
| `MappedID` | `Listing_MappedID` |
| `Hotel` | `Listing_Name` |
| `IceID` | `Listing_ICEID` |
| `AssetType` | `Asset_MediaType` |
| `Index` | `Asset_Index` |
| `PublicID` | `Asset_PublicID` |
| `Caption` | `Asset_Caption` |
| `URL` | `Asset_Link` |

Quando legge il formato ICE/export, la pipeline preserva le colonne originali e
aggiunge le colonne interne necessarie all'elaborazione.

Esempio di run:

```bash
python pipeline.py --input data.csv --propid 77519
```

## Configurazione AI In `prompts.yaml`

`prompts.yaml` controlla modelli, tono, prompt, taxonomy e provider di generazione testi.

### Modello Top-Level

Il campo top-level `model` controlla la classificazione amenity Gemini:

```yaml
model: gemini-3.1-flash-lite
```

Questa classificazione usa sempre Gemini, anche quando i testi sono generati via OpenRouter.

### Content Generation

La sezione `content_generation` controlla solo questi campi:

- `Caption_Experience`
- `Description_Experience`
- `Alt_Text`
- `Check_Room`

Esempio con Gemini:

```yaml
content_generation:
  provider: gemini
  model: gemini-3.1-flash-lite
  temperature: 1.0
  max_tokens: 500
  thinking_budget: 0
```

Esempio con OpenRouter:

```yaml
content_generation:
  provider: openrouter
  model: openai/gpt-4o-mini
  temperature: 1.0
  max_tokens: 500
  thinking_budget: 0
```

Con `provider: openrouter`, la pipeline invia l'immagine come data URL base64 alla Chat Completions API di OpenRouter. Quindi il modello OpenRouter vede direttamente la foto: non e una semplice riscrittura di un testo Gemini.

Con `provider: gemini`, la pipeline usa la stessa `GEMINI_API_KEY` sia per classificare sia per generare i testi. `OPENROUTER_API_KEY` non serve.

### Temperature E Creativita

`temperature` influenza la varieta dei testi generati:

- valori bassi, ad esempio `0.2` o `0.4`, producono output piu conservativi e ripetitivi;
- valori intorno a `0.8` o `1.0` producono copy piu vari;
- valori troppo alti possono aumentare il rischio di frasi meno controllate.

Per copy evocativo ma ancora coerente con l'immagine, il valore consigliato e:

```yaml
temperature: 1.0
```

`max_tokens` limita la lunghezza massima della risposta del modello, non la lunghezza finale dei singoli campi. I limiti editoriali vengono spiegati nel prompt.

### Instructions

`content_generation.instructions` contiene le regole editoriali specifiche per caption, description, alt text e check room.

Qui puoi definire, ad esempio:

- tono di voce;
- stile evocativo;
- limiti caratteri;
- divieto di inventare dettagli non visibili;
- richiesta di maggiore varieta tra immagini simili;
- formato JSON richiesto.

La pipeline si aspetta che il modello risponda con JSON valido e queste chiavi:

```json
{
  "Caption_Experience": "...",
  "Description_Experience": "...",
  "Alt_Text": "...",
  "Check_Room": "0"
}
```

## Amenity Taxonomy

La sezione `amenity_taxonomy` e il cuore della catalogazione immagini.

Ogni voce rappresenta una categoria possibile:

```yaml
amenity_taxonomy:
  - category: "Spa / Wellness Center"
    keywords: "Use for images where a spa or wellness facility is clearly visible..."
    codes: "12"
    maxcategoria: "Spa"
    custom_tag_1: "overview-spa"
    custom_tag_2: "amenity-spa"
    custom_tag_3: "experience-spa-N"
    custom_tag_4: "spa-attribute-N"
```

### `category`

Nome della categoria che l'AI puo assegnare all'immagine.

Deve essere chiaro e stabile, perche viene riportato in `Amenity_Category` e poi nell'export Excel come `Category`.

### `keywords`

`keywords` non e solo una lista di parole chiave. E un mini prompt per spiegare all'AI come catalogare le immagini.

Deve dire:

- quando usare quella categoria;
- quali elementi visivi devono essere presenti;
- quando non usare quella categoria;
- quali categorie simili evitare se il contesto non e corretto.

Esempio buono:

```yaml
keywords: "Use for images where a spa or wellness facility is clearly visible, including treatment rooms, massage tables, sauna, steam room, spa reception, wellness corridors, indoor spa pools, hydrotherapy areas, or calm wellness spaces. Do not use for standard guest bathrooms, generic pools, fitness rooms, or ordinary lounge seating."
```

Questa forma aiuta il modello a distinguere immagini simili, ad esempio una lounge normale da una spa lounge, oppure una piscina generica da una spa pool.

### `codes`

Codici amenity associati alla categoria.

Vengono scritti in:

```text
Amenity_Codes
```

### `maxcategoria`

Macro categoria usata per raggruppare la categoria specifica.

Viene scritta in:

```text
Amenity_MaxCategory
```

### Custom Tag

Ogni categoria puo avere fino a quattro custom tag:

```yaml
custom_tag_1: "overview-spa"
custom_tag_2: "amenity-spa"
custom_tag_3: "experience-spa-N"
custom_tag_4: "spa-attribute-N"
```

La pipeline scrive:

- `Amenity_CustomTag1`
- `Amenity_CustomTag2`
- `Amenity_CustomTag3`
- `Amenity_CustomTag4`
- `Amenity_CustomTags`

`Amenity_CustomTags` e la concatenazione dei quattro campi non vuoti.

### Numerazione Automatica Dei Tag `-N`

Se un custom tag finisce esattamente con `-N`, la pipeline sostituisce `N` con un indice progressivo per hotel.

Esempio:

```text
spa-attribute-N -> spa-attribute-1
spa-attribute-N -> spa-attribute-2
```

Regole:

- la numerazione riparte da `1` per ogni hotel;
- i contatori sono separati per template/tag;
- se nella stessa riga compaiono piu template `-N`, ricevono lo stesso indice;
- `Amenity_CustomTags` viene rigenerato dopo la sostituzione;
- tag senza suffisso esatto `-N` restano invariati.

Esempio:

```text
Riga 1: experience-spa-N + spa-attribute-N -> experience-spa-1 + spa-attribute-1
Riga 2: spa-attribute-N -> spa-attribute-2
Nuovo hotel: spa-attribute-N -> spa-attribute-1
```

## Categoria `Other`

Se la classificazione non trova una categoria affidabile, la pipeline usa:

```text
Amenity_Category = Other
```

Questo puo succedere quando:

- il modello restituisce `Other`;
- lo score e sotto soglia;
- la categoria restituita non e presente nella taxonomy;
- la classificazione fallisce e viene applicato il fallback.

Le immagini `Other` restano nell'output finale, ma non devono generare testi. In quel caso i campi content sono:

```text
Caption_Experience = ""
Description_Experience = ""
Alt_Text = ""
Check_Room = "0"
```

Nei log lo step `generation` riporta:

```json
{
  "skipped": true,
  "skip_reason": "amenity_category_other"
}
```

## Comandi Pipeline

### Singolo Hotel Per Propid

```bash
python pipeline.py --input data.csv --propid 77519
```

### Piu Hotel Per Propid

```bash
python pipeline.py --input data.csv --propid 77519,98373
```

### Hotel Per Nome Esatto

```bash
python pipeline.py --input data.csv --hotel-name "Zante Park Resort & Spa, BEST WESTERN Premier Collection"
```

### Lista Hotel Da File

`hotels.txt` deve contenere un nome hotel per riga.

```bash
python pipeline.py --input data.csv --hotel-name-file hotels.txt
```

### Prossimi Hotel Non Ancora Processati

```bash
python pipeline.py --input data.csv --next-hotels 3
```

### Rigenerare Output Esistenti

```bash
python pipeline.py --input data.csv --propid 77519 --force
```

### Worker Paralleli

```bash
python pipeline.py --input data.csv --next-hotels 10 --workers 15
```

Default:

```text
--workers 5
```

Valori piu alti velocizzano la run ma aumentano il rischio di rate limit API. Un valore pratico e spesso:

```text
--workers 15
```

### Debug Log

```bash
python pipeline.py --input data.csv --propid 77519 --debug-log
```

Con `--debug-log` vengono scritti dettagli su:

- retry;
- errori HTTP;
- body HTTP sintetizzato;
- parse failure;
- checkpoint hit;
- provider e modello usati per generazione.

### Dry Run

```bash
python pipeline.py --input data.csv --next-hotels 5 --workers 15 --dry-run
```

Il dry run non processa immagini. Stima:

- numero immagini;
- numero chiamate API;
- costo stimato;
- tempo indicativo.

## Output Pipeline

Per ogni hotel viene creato un CSV:

```text
output_hotels/<Propid>_<hotel-name-slug>.csv
```

Inoltre viene mantenuto un cumulativo:

```text
output_hotels/all_hotels_cumulative.csv
```

Quando un hotel viene rigenerato, le sue righe nel cumulativo vengono sostituite con la versione piu recente.

Colonne aggiunte dalla pipeline:

```text
Amenity_Category
Amenity_Codes
Amenity_MaxCategory
Amenity_CustomTag1
Amenity_CustomTag2
Amenity_CustomTag3
Amenity_CustomTag4
Amenity_CustomTags
Caption_Experience
Description_Experience
Alt_Text
Check_Room
```

## Checkpoint E Resume

Durante il processamento di un hotel, la pipeline crea un file sidecar:

```text
output_hotels/<Propid>_<hotel-name-slug>.progress.jsonl
```

Serve per recuperare il lavoro gia completato se la run si interrompe.

Per riprendere:

```bash
python pipeline.py --input data.csv --propid 77519
```

La pipeline rilegge il checkpoint e processa solo le immagini mancanti. Quando l'hotel finisce correttamente, il sidecar viene eliminato.

## Export Excel

Dopo aver generato il cumulativo:

```bash
python export_content_excel.py
```

Default:

```text
input:  output_hotels/all_hotels_cumulative.csv
output: output_hotels/content_export.xlsx
delimiter: ;
```

Puoi anche specificare input/output:

```bash
python export_content_excel.py --input output_hotels/all_hotels_cumulative.csv --output output_hotels/content_export.xlsx
```

L'Excel ha:

- prima riga congelata;
- autofilter;
- colonne ordinate per upload/content curation.

Colonne finali Excel:

```text
IceID
MappedID
Hotel
AssetType
Index
PublicID
Category
Custom Tags
Caption
Description
Alt Text
URL
```

Mappatura:

| Excel | CSV pipeline |
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

L'export non usa eventuali colonne legacy `Caption` e `Description`. Usa sempre `Caption_Experience` e `Description_Experience`.

## Workflow Consigliato

1. Configura `.env` o i secret Codespaces.
2. Controlla `prompts.yaml`, soprattutto provider, modello e taxonomy.
3. Esegui un dry run:

```bash
python pipeline.py --input data.csv --next-hotels 3 --workers 15 --dry-run
```

4. Processa pochi hotel di test:

```bash
python pipeline.py --input data.csv --next-hotels 3 --workers 15 --debug-log
```

5. Genera Excel:

```bash
python export_content_excel.py
```

6. Controlla `content_export.xlsx`.
7. Se serve rigenerare, usa `--force`.

## Test

Esegui tutti i test:

```bash
python -m unittest
```

Verifica manuale completa:

```bash
python -m unittest
python pipeline.py --input data.csv --propid 77519 --force --debug-log
python export_content_excel.py
```

## Troubleshooting

### `Missing Gemini API key`

Imposta `GEMINI_API_KEY`. Serve sempre per la classificazione amenity.

### `Missing OpenRouter API key`

Succede solo se:

```yaml
content_generation:
  provider: openrouter
```

Soluzioni:

- imposta `OPENROUTER_API_KEY`; oppure
- cambia provider in `gemini`.

### Provider Non Supportato

`content_generation.provider` accetta solo:

```text
gemini
openrouter
```

### Output Gia Esistente

Se il CSV hotel esiste gia e non usi `--force`, la pipeline lo salta e sincronizza comunque il cumulativo.

Per rigenerare:

```bash
python pipeline.py --input data.csv --propid 77519 --force
```

### Test Falliscono Dopo Patch Locali

Riesegui:

```bash
python -m unittest
```

Se hai applicato patch locali non ancora committate, controlla:

```bash
git status
```

### Copy Troppo Ripetitivi

Intervieni in `prompts.yaml`:

- aumenta o mantieni `temperature: 1.0`;
- rafforza `content_generation.instructions` chiedendo piu varieta;
- evita istruzioni troppo rigide o formule ricorrenti;
- mantieni comunque il vincolo di non inventare dettagli non visibili.

### Classificazione Sbagliata

Intervieni sulla voce `keywords` della categoria interessata.

Ricorda: `keywords` e un mini prompt. Spiega meglio quando usare o non usare la categoria, soprattutto nei casi simili o ambigui.
