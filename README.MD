# Lab 5 starter

Drop these files into your Lab 5 repository **on top of** your Lab 4 work.
They replace `client.py`, `stub_client.py`, `check.py`, `.gitignore`,
`.env.example` and `requirements.txt`, and add three new files.

`SPEC.md`, `DECISIONS.md`, `QUESTION.md`, `main.py` and `samples/*.json`
are deliberately **not** in this zip, so that copying it over your Lab 4
repository cannot destroy your own work.

| File | Yours? |
|---|---|
| `client.py` | No. Do not edit. Identical to the Lab 4 file |
| `stub_client.py` | No. Do not edit. Now ten modes, not eight |
| `check.py` | No. The same script used for marking. Run it on yourself |
| `observability.py` | **Supplied, and yours to edit.** Set the two prices. Add fields if you want. Never rename the nine keys |
| `REPORT.md` | Yes. Three fixed headings. The answer you give the client |
| `.gitignore` | Replaces the Lab 4 one. Adds `run_log.jsonl` |
| `.env.example` | Copy to `.env` and fill in. Never commit `.env` |
| `samples/run_log_sample.jsonl` | Yes. Create it. At least 20 lines from your own log |

## Start

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
cp .env.example .env
```

## Run

```bash
python main.py "photosynthesis"
```

## Check yourself

```bash
python check.py .
```

## The ten stub modes

```
stub:ok  stub:fenced  stub:preamble  stub:malformed  stub:badshape
stub:empty  stub:refused  stub:error  stub:flaky  stub:ratelimit
```

Read the assignment brief for what is fixed, what is yours, and how it is marked.
