# desktop/models/

## How it works

- `catalog.CATALOG`: each model the app offers. `ModelSpec`: id (folder name), title, backend (`faster-whisper` or `parakeet-mlx`), Hugging Face repo, revision (commit), files with their sizes, note.
- `catalog.available()`: the models this computer can run. Parakeet-mlx models only on Apple Silicon.
- An installed model is the folder `<data>/models/<id>/`. `commands.engine_args` gives it to the CLIs as `--backend <backend> --model <folder>`.
- `download.download(model, root, progress, cancelled)`: each file to `<root>/<id>.partial/<name>.part` (HTTP Range continues a `.part` left by a cancel or a dropped connection), size check per file, then rename of the files and of the folder to `<root>/<id>/`.

| Model | Backend | Size |
|---|---|---|
| Whisper Tiny, Base, Small, Medium, Large v3, Large v3 Turbo | faster-whisper (CTranslate2, CPU int8) | 78 MB - 3.1 GB |
| Parakeet v2 (English), Parakeet v3 (25 languages) | parakeet-mlx (Apple Silicon GPU) | 2.5 GB |

## Decisions

- Models are not in the app: the app stays small, and the user downloads only the model they choose (as OpenSuperWhisper does).
- A fixed revision and file list per model: each download gets the same files, and the size check finds a cut or changed file. A repo update needs a catalog change; the files list is short.
- Only the files the engine reads are downloaded (no README, no `.gitattributes`). Parakeet needs only `config.json` and `model.safetensors`.
- Own downloader on `httpx`, not `huggingface_hub.snapshot_download`: it gives byte progress for the bar, a cancel between chunks, and a resume of a 3 GB file after a dropped connection, with no cache folder layout to look into.
- The folder is renamed only when all files are complete. Thus "the folder exists" means "installed", and a partial download is never loaded.
- `--model <folder>` and not a model name: faster-whisper loads a folder with no network call, and `app.stages.common.parakeet.load` loads a folder with no Hugging Face call (`parakeet_mlx.from_pretrained` tries the network first even for a folder).
