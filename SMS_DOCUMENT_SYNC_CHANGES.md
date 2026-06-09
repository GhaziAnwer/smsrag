# Safe Incremental Indexing Change

Ye documentation us change ke liye hai jisme `sync_sms_documents.py` ko safer banaya gaya, taaki changed HTML files ka index update ho, lekin old/unchanged files ke chunks accidentally delete ya overwrite na ho.

## Problem kya thi

Pehle indexing flow me do risky cases the:

1. `chunks.jsonl` rewrite risk

   Agar 100 files pehle se indexed hain aur sirf 1 file change hui, to galat rewrite logic ki wajah se `chunks.jsonl` me sirf us 1 changed file ke chunks reh sakte the. Baaki 99 files ke chunks lost ho sakte the.

2. Chroma update risk

   Chroma me agar same file update hui aur chunk IDs same rahe, to old records properly replace nahi hote the. Logic sirf missing IDs insert kar raha tha:

   ```python
   existing_ids = set(chroma_collection.get()["ids"])
   new_chunks = [c for c in all_chunks if c.id_ not in existing_ids]
   ```

   Iska issue ye tha ki agar chunk ID same hai but content changed hai, to updated content Chroma me overwrite nahi hota tha. Result: app old answer de sakta tha.

## Kya change kiya

File: `tools/sync_sms_documents.py`

### 1. Changed file detection safe banaya

Function: `copy_changed_files`

- Source repo ke client documents folder se `.html` / `.htm` files check hoti hain.
- Destination file se SHA256 hash compare hota hai.
- Sirf new/changed files copy hoti hain.
- Same content wali files skip hoti hain.
- Existing destination file overwrite karne se pehle backup banta hai.
- Destination-only files delete nahi hoti, sirf log hoti hain.

## 2. Chroma records file-wise replace hote hain

Functions:

- `backup_chroma_records`
- `delete_chroma_records`
- `insert_file_chunks`

Changed file ke liye ab flow ye hai:

1. Us file ke existing Chroma records backup hote hain.
2. Us file ke old Chroma records delete hote hain.
3. New chunks insert hote hain.
4. Insert ke baad confirmation hota hai ki expected chunk IDs Chroma me aa gaye.
5. Agar insert fail hota hai, old Chroma records restore karne ki try hoti hai.

Iska benefit:

- Same chunk ID hone ke baad bhi updated content replace hota hai.
- Old stale content Chroma me nahi rehta.
- Failed indexing ke case me rollback backup available rehta hai.

## 3. `chunks.jsonl` unchanged files preserve karta hai

Function: `rewrite_chunks_jsonl`

Ab `chunks.jsonl` rewrite karte waqt:

- Pehle existing `chunks.jsonl` ka backup banta hai.
- Existing rows read hoti hain.
- Sirf changed file ke old rows remove hote hain.
- Unchanged files ke rows preserve hote hain.
- Changed file ke new chunks append hote hain.
- Final file atomic replace se write hoti hai.

Example:

- Pehle index: 100 files
- Change: sirf `abc.html`
- Result:
  - `abc.html` ke old chunks remove
  - `abc.html` ke new chunks add
  - Baaki 99 files ke chunks same rahenge

## 4. Manifest update confirmation ke baad hota hai

Function: `run_incremental_index`

Manifest update ab tabhi hota hai jab:

- changed file ke chunks build ho gaye
- Chroma insert successful ho gaya
- Chroma confirmation pass ho gaya
- `chunks.jsonl` safely rewrite ho gaya

Isse manifest galat state me update nahi hota. Agar indexing fail hoti hai, to manifest old state me reh sakta hai, jo safer hai.

## Kyon ye change kiya

- Full re-index har baar costly aur slow hai.
- Incremental indexing fast hai, but unsafe implementation se old data loss/stale data ka risk tha.
- Chroma me same ID ka content automatically replace nahi ho raha tha.
- `chunks.jsonl` me unchanged file rows preserve karna zaroori tha.
- Production me document update ke baad answers latest document se aane chahiye.

## Final behavior

Ab jab ek file change hoti hai:

1. Sirf wahi file copy hoti hai.
2. Sirf wahi file re-chunk hoti hai.
3. Sirf us file ke Chroma records replace hote hain.
4. `chunks.jsonl` me baaki files ke chunks preserve rehte hain.
5. Manifest successful Chroma update ke baad hi update hota hai.

Short answer:

`indexer_section_wise.py` parser/chunker/enrichment logic provide karta hai. `sync_sms_documents.py` us logic ko safe wrapper ke saath use karta hai, jahan changed-file replacement, Chroma cleanup, Chroma insert confirmation, aur `chunks.jsonl` preservation handle hota hai.

---

# `tools/sync_sms_documents.py` Code Explanation

Neeche explanation line-number based hai. Code ko exact file ke saath side-by-side dekhne ke liye `nl -ba tools/sync_sms_documents.py` use kar sakte hain.

## Lines 1-11: File purpose

- Line 1: Script ko directly executable Python script banata hai.
- Lines 2-11: Docstring batata hai ki script daily SMS document sync automation ke liye hai.
- Iska main kaam:
  - source documents repo pull karna
  - sirf new/changed HTML files copy karna
  - sirf changed clients ka index update karna
  - secrets ko environment variables se read karna

Kyon kiya: scheduler/cron me script self-contained aur clear purpose ke saath run ho sake. Tokens code me hardcode na hon.

## Lines 13-28: Imports

- Line 13: Future annotations type hints ko runtime me light banata hai.
- Lines 15-28: Required standard libraries import hoti hain:
  - `argparse`: command-line options ke liye
  - `base64`: Git auth header banane ke liye
  - `hashlib`: file SHA256 compare ke liye
  - `json`: manifest/chunks backup/write ke liye
  - `logging`: logs ke liye
  - `os`: env vars, atomic replace ke liye
  - `shutil`: file copy/backup ke liye
  - `subprocess`: git commands run karne ke liye
  - `contextmanager`: lock file helper ke liye
  - `dataclass`: client result structure ke liye
  - `datetime/timezone`: timestamp ke liye
  - `Path`: safe path handling ke liye
  - `Iterable`: type hint ke liye
  - `urlparse/urlunparse`: URL/token masking ke liye

Kyon kiya: script ko shell string manipulation ke bajay structured Python APIs se safe banane ke liye.

## Lines 31-36: Defaults and globals

- Line 31: Default clients define kiye: `rsms`, `oceangold`, `supereco`, `primenova`, `prime`.
- Line 32: Default source repo branch set ki.
- Line 33: Default GitLab project path set kiya.
- Line 35: Dedicated logger banaya.
- Line 36: `_INDEXING_IMPORTS_READY` flag rakha.

Kyon kiya: defaults se cron simple rahe, aur env vars se override possible rahe. Import flag heavy indexing libraries ko lazy-load karne ke liye hai.

## Lines 39-50: `ClientResult`

- Lines 39-46: Har client ke sync result ka data model:
  - client name
  - checked hua ya nahi
  - changed files
  - copied files
  - indexed hua ya nahi
  - error message
- Lines 48-50: Agar lists `None` hain to empty list set karta hai.

Kyon kiya: final summary reliable banane ke liye. Har client ka success/failure separately track hota hai.

## Lines 53-58: Small helpers

- `utc_stamp`: UTC timestamp string banata hai, backup folder/file names ke liye.
- `repo_root`: current script se project root find karta hai.

Kyon kiya: backup names unique rahein aur paths hardcoded na hon.

## Lines 61-70: `parse_clients`

- Input comma-separated clients string leta hai.
- Empty value ho to default clients use karta hai.
- Spaces remove karta hai.
- Lowercase karta hai.
- Duplicate clients remove karta hai while order preserve karta hai.

Kyon kiya: `.env` ya CLI me client list flexible rahe aur duplicate processing na ho.

## Lines 73-86: File content compare

- `sha256_file`: file ko chunks me read karke SHA256 hash banata hai.
- `same_file_content`:
  - destination file exist nahi karti to `False`
  - size different ho to `False`
  - size same ho to SHA256 compare

Kyon kiya: mtime ya filename par depend nahi kiya. Sirf actual content changed ho to copy/index chale.

## Lines 89-123: `ensure_indexing_imports`

- Heavy imports tabhi load hote hain jab indexing actually needed ho.
- Chroma, LlamaIndex, OpenAIEmbedding import hota hai.
- Existing `indexer_section_wise.py` ke parser/chunker/enrichment helpers import hote hain.
- `_INDEXING_IMPORTS_READY = True` set hota hai.

Kyon kiya:

- Agar koi document change nahi hai, to heavy RAG dependencies load karne ki zaroorat nahi.
- Existing tested indexing logic reuse hota hai.
- Same parser/chunker use karne se old index behavior consistent rehta hai.

## Lines 126-147: Secret masking

- `mask_secret`: logs me token hide karta hai.
- `re_mask_basic_auth`: Git basic auth header ko `***` se replace karta hai.
- URL me username/password ho to netloc clean karke return karta hai.

Kyon kiya: logs me Git token leak na ho. Production logs usually shared/debugged hote hain.

## Lines 150-173: `run_cmd`

- Git command safe log format me prepare hoti hai.
- `GIT_TERMINAL_PROMPT=0` set hota hai, taaki cron me prompt hang na kare.
- Command run hoti hai with stdout/stderr capture.
- Failure par masked stdout/stderr ke saath exception raise hota hai.
- Success par stdout return hota hai.

Kyon kiya: Git failures visible rahein, but secrets hidden rahein. Cron me command silently hang na ho.

## Lines 176-193: `build_repo_url`

- Env `SMS_DOCS_REPO_URL` present ho to use karta hai.
- Nahi to host/project path se URL banata hai:
  - default host: `gitlab.com`
  - default project: `SMS-REPO/live-sms-documents.git`

Kyon kiya: repo URL direct bhi set ho sake, aur GitLab defaults se minimal config me bhi run ho sake.

## Lines 196-224: `git_auth_args`

- Token env vars read karta hai:
  - `SMS_DOCS_GIT_TOKEN`
  - `SMS_DOCS_GITHUB_TOKEN`
  - `SMS_DOCS_GITLAB_TOKEN`
- HTTP/HTTPS URL ho aur URL me already credentials na hon tab auth header banata hai.
- GitHub ke liye default username `x-access-token`, GitLab ke liye `oauth2`.
- Basic auth header base64 encode karke Git `-c http.extraHeader=...` args return karta hai.

Kyon kiya: token remote URL me save na ho. Clone/pull ke liye temporary auth use ho.

## Lines 227-271: `ensure_repo`

- Repo dir resolve hota hai.
- Repo URL aur auth args prepare hote hain.
- Agar local repo already exists:
  - `--skip-pull` ho to pull skip
  - `--dry-run` ho to sirf log
  - warna `git fetch`, `git checkout`, `git pull --ff-only`
- Agar repo missing hai:
  - repo URL missing ho to error
  - dry-run ho to clone preview
  - warna parent dir create karke single branch clone

Kyon kiya:

- Scheduler source repo ka latest data pull kare.
- `--ff-only` merge commits/conflicts avoid karta hai.
- Dry-run safe testing ke liye hai.
- Skip-pull local testing/debugging ke liye hai.

## Lines 274-285: HTML file discovery

- `html_files`: `.html` aur `.htm` files list karta hai.
- `detect_destination_only_files`: destination me extra HTML files find karta hai.

Kyon kiya: sirf supported document files process hon. Destination-only files delete nahi karte because manual/legacy files accidentally remove ho sakti hain.

## Lines 288-297: `backup_existing_file`

- Destination file exist nahi karti to backup nahi banta.
- Backup folder create hota hai.
- Same backup name already ho to timestamp add hota hai.
- `shutil.copy2` metadata ke saath backup copy karta hai.

Kyon kiya: overwrite se pehle rollback copy available rahe.

## Lines 300-356: `copy_changed_files`

- Lines 300-306: Function inputs: client, source docs path, target docs path, backup root, dry-run.
- Lines 307-308: `changed` aur `copied` lists initialize.
- Lines 310-312: Source folder validate.
- Lines 314-315: Source HTML files collect and log.
- Lines 317-328: Target folder create, destination-only files log.
- Line 329: Client-specific backup folder with timestamp.
- Lines 331-334: Har source file ke liye destination path banata hai aur same content ho to skip.
- Line 336: Changed list me filename add.
- Lines 337-339: Dry-run me copy nahi karta, sirf log.
- Lines 341-343: Existing file backup karta hai.
- Lines 345-347: Temp file me copy, phir `os.replace` se atomic replace.
- Lines 348-349: Copied list update and log.
- Lines 351-356: Summary log and return.

Kyon kiya:

- Sirf changed file index ho.
- Partial copy se broken HTML na aaye, isliye temp + atomic replace.
- Dry-run se actual copy/index ke bina behavior test ho sake.

## Lines 359-382: `create_stub_node`

- File name se default title banata hai.
- HTML me first heading mile to title usse replace karta hai.
- `TextNode` metadata ke saath stub chunk banata hai.
- Stable node ID assign karta hai.

Kyon kiya: image-only ya low-text files ke liye bhi index me minimal searchable entry rahe.

## Lines 385-398: `load_chunks_jsonl`

- `chunks.jsonl` missing ho to empty list return.
- File line-by-line read hoti hai.
- Empty lines skip hoti hain.
- Valid JSON rows list me add hoti hain.
- Invalid JSON row par warning log hoti hai.

Kyon kiya: existing chunks safely read ho, aur ek bad row se full sync fail na ho.

## Lines 401-425: `rewrite_chunks_jsonl`

- Backup dir create.
- Existing `chunks.jsonl` ka backup.
- Existing rows read.
- Changed files ke old rows remove.
- Unchanged files ke rows preserve.
- New chunks ko JSON rows me convert.
- Temp file write.
- `os.replace` se atomic final replace.

Kyon kiya: screenshot wali main problem yahi solve hoti hai. 1 changed file ke chakkar me baaki 99 files ke chunks lost nahi hote.

## Lines 428-435: `json_safe`

- Numpy/list-like value me `.tolist()` ho to normal list banata hai.
- Dict/list recursively safe banata hai.

Kyon kiya: Chroma embeddings kabhi JSON serializable direct nahi hote. Backup JSON me write karne ke liye safe conversion chahiye.

## Lines 438-452: `backup_chroma_records`

- Backup folder create.
- Chroma se given filename ke documents/metadatas/embeddings fetch.
- Agar records exist karte hain, JSON backup file write hoti hai.
- Records return hote hain.

Kyon kiya: old Chroma records delete karne se pehle rollback data safe rahe.

## Lines 455-464: `restore_chroma_records`

- Backup records me IDs nahi hain to return.
- IDs, documents, metadatas, embeddings Chroma me wapas add karta hai.

Kyon kiya: insert failure ke case me old index restore karne ki koshish ho.

## Lines 467-473: `delete_chroma_records`

- Chroma se given filename ke IDs get karta hai.
- IDs mile to delete karta hai.
- Delete count return karta hai.

Kyon kiya: same chunk ID wale stale content ko replace karne ke liye pehle old records delete karna zaroori hai.

## Lines 476-496: `insert_file_chunks`

- Old Chroma records backup.
- Old records delete.
- Empty `VectorStoreIndex` storage context ke saath create.
- New chunks insert.
- Insert fail ho to old records restore and exception re-raise.
- Insert ke baad Chroma se IDs confirm.
- Missing IDs mile to error.

Kyon kiya:

- Upsert-style stale data problem avoid hoti hai.
- Confirmation ke bina manifest update nahi hona chahiye.
- Failure me rollback attempt available rahe.

## Lines 499-523: `build_file_chunks`

- HTML file read.
- `_Toc` sections extract.
- Section-wise chunks build.
- Chunks nahi bane to stub node create.
- Manifest entry banata hai:
  - SHA256
  - mtime
  - sections count
  - chunks count
  - stub flag

Kyon kiya: changed file ko same production chunking rules se re-index karne ke liye.

## Lines 526-539: `write_index_settings`

- `settings.json` write karta hai.
- Embed model, chunk size, overlap, strategy, last_updated store hota hai.

Kyon kiya: later debugging me pata rahe ki index kis settings se bana.

## Lines 542-612: `run_incremental_index`

- Changed filenames deduplicate/sort.
- Dry-run me indexing skip, sirf log.
- Empty list ho to return.
- Heavy indexing imports load.
- Client docs/index/chroma/manifest/settings paths set.
- Rules/synonyms/manifest load.
- OpenAI embedding model configure.
- Persistent Chroma client open.
- Chroma collection/vector store/storage context setup.
- Har changed file:
  - file exists check
  - chunks build
  - rules/synonyms enrichment
  - Chroma old records replace with new chunks
  - manifest update data collect
- Agar new chunks inserted:
  - `chunks.jsonl` safely rewrite
  - manifest update
  - settings update

Kyon kiya: full re-index ke bajay sirf changed files update karne ke liye, but Chroma + chunks + manifest ko consistent order me update karne ke liye.

## Lines 615-636: `lock_file`

- Lock file parent folder create.
- File open.
- `fcntl.flock` exclusive non-blocking lock apply.
- Already running job ho to error.
- PID/start time lock file me write.
- Finally lock release and file close.

Kyon kiya: cron overlap se duplicate indexing/corrupt writes ka risk avoid hota hai.

## Lines 639-650: `setup_logging`

- Log file folder create.
- Verbose ho to DEBUG, warna INFO.
- Console + file logging setup.
- `force=True` existing logging config replace karta hai.

Kyon kiya: scheduler logs file me bhi milen aur terminal me bhi.

## Lines 653-703: `process_clients`

- Client list parse.
- Source repo clone/pull ensure.
- Dry-run clone preview case me stop.
- Har client ke liye:
  - source documents path
  - target client root
  - target documents path
  - changed/copy detection
  - result fields update
  - changed/copied files ho to incremental index
  - error aaye to current client fail mark, next client continue

Kyon kiya: ek client fail ho to baaki clients ka sync rukna nahi chahiye.

## Lines 706-758: `build_parser`

CLI options define karta hai:

- `--repo-dir`: source repo local clone path
- `--branch`: source branch
- `--data-dir`: app data/index root
- `--clients`: client list
- `--rules`: `rules.yaml` path
- `--backup-dir`: document backup location
- `--log-file`: log file
- `--lock-file`: overlap lock
- `--skip-pull`: existing repo use
- `--dry-run`: no copy/index
- `--verbose`: debug logs

Kyon kiya: same script local test, server run, cron, and debugging sab me configurable rahe.

## Lines 761-809: `main`

- Parser build and args parse.
- Paths expand/resolve.
- Missing optional paths ke defaults set:
  - repo dir under data dir
  - backup dir under data dir
  - log file under data dir
  - lock file under data dir
- Logging setup.
- Startup config log.
- Lock ke andar `process_clients` run.
- Early failure ho to exit `1`.
- Results se failures/indexed/changed summary build.
- Failures log.
- Failure count zero ho to exit `0`, warna `1`.
- Script direct run ho to `main()` execute.

Kyon kiya: command-line script proper Unix exit code de, scheduler success/failure correctly detect kar sake.

## Overall flow summary

1. Config/env/CLI read hota hai.
2. Lock acquire hota hai.
3. Source documents repo clone/pull hota hai.
4. Har client ke HTML files compare hote hain.
5. Sirf changed files copy hote hain.
6. Sirf changed files ka index update hota hai.
7. Changed file ke old Chroma records replace hote hain.
8. `chunks.jsonl` me unchanged files preserve rehte hain.
9. Manifest/settings successful update ke baad write hote hain.
10. Summary log + exit code return hota hai.
